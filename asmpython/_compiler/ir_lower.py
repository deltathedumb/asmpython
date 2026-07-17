"""Lower asmpython's AST (post-sema) to the SSA IR in ir.py, for handoff to
the built-in x86-64 backend (asmpython/_backends/x86_64's
run_backend_codegen) -- reached via driver.py's --backend x86-64.

Current scope: int arithmetic, comparisons, if/while, return, calls
between asmpython functions, print(int|str), class instantiation +
attribute get/set (no-arg constructors only, via the ABI shim layer --
see abi_shims.asm), and asmlib.hardware's FFI bindings. Still missing:
floats, lists, real __init__ wiring, and most string operations -- see
ir_lower.py's open items in the project's own tracking, not duplicated
here.

Every local variable gets its own stack slot (`alloca` + `load`/`store`)
rather than being threaded through real SSA values with phi nodes at
control-flow merges. This is the standard "memory SSA" simplification
(what e.g. clang -O0 emits before mem2reg): it's always correct regardless
of which branch of an if/while ran, because a `load` after a merge point
just reads whatever was last `store`d on whichever path executed, and it
sidesteps needing dominance-frontier phi insertion entirely. Expression
temporaries (binop/call results) ARE real single-assignment SSA values,
since nothing ever overwrites them.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A
from .ir import (
    IRBlock,
    IRFunc,
    IRGlobal,
    IRInstr,
    IRModule,
    IRType,
    IRValue,
    F64,
    I64,
    PTR,
    ir_type_for,
)
from .codegen import BUILTIN_EXC_IDS, BUILTIN_EXC_PARENTS, BUILTIN_TYPE_IDS, EXC_ANY


class LowerError(Exception):
    pass


U8 = IRType("u8")

# jmp_buf layout (mirrors _runtime_setjmp in codegen.py): rbx/rbp/r12-r15/
# rsp/retaddr in the first 8 slots (0-56), rsi/rdi in slots 8-9 (64, 72) --
# 10 regs * 8 bytes = 80 bytes. rsi/rdi were added after the first 8 slots
# were already established elsewhere; kept at the end rather than
# renumbering to avoid touching every other offset in this file.
_JMP_BUF_SIZE = 80

# Build parent-id map once at module level from BUILTIN_EXC_PARENTS.
_EXC_PARENT_OF: dict[int, int] = {
    BUILTIN_EXC_IDS[k]: (BUILTIN_EXC_IDS[v] if v else -1)
    for k, v in BUILTIN_EXC_PARENTS.items()
    if k in BUILTIN_EXC_IDS
}


def _exc_raise_type_id_ir(value) -> int:
    """Type id for `raise value` -- mirrors codegen._exc_raise_type_id."""
    name = None
    if isinstance(value, A.Call):
        name = value.func
    elif isinstance(value, A.Name):
        name = value.name
    if name is not None and name in BUILTIN_EXC_IDS:
        return BUILTIN_EXC_IDS[name]
    return EXC_ANY


def _exc_matching_ids_ir(types: list) -> list:
    """Full set of type ids an `except (T1, T2, ...):` catches,
    including subtypes and EXC_ANY -- mirrors codegen._exc_matching_ids."""
    ancestor_ids = [BUILTIN_EXC_IDS[t] for t in types if t in BUILTIN_EXC_IDS]
    matches: list = [EXC_ANY]
    for a in ancestor_ids:
        if a not in matches:
            matches.append(a)
    for tid in BUILTIN_EXC_IDS.values():
        for a in ancestor_ids:
            cur = tid
            seen: list = []
            while cur >= 0 and cur not in seen:
                if cur == a:
                    if tid not in matches:
                        matches.append(tid)
                    break
                seen.append(cur)
                cur = _EXC_PARENT_OF.get(cur, -1)
    return matches


class _ModuleCtx:
    """Shared across every function lowered in one module: interns string
    literals (and runtime format strings) as deduplicated IRGlobal entries,
    tracks which names are classes so `ClassName(...)` lowers to
    instantiation rather than an ordinary call, and holds the FFI surface
    (stdlib.Func bindings, e.g. asmlib.hardware's in_byte/cpuid/...) so a
    bare call to one of those names lowers to a call against its real
    c_name symbol instead of treating the asmpython-level name as a label."""

    def __init__(
        self,
        class_names: frozenset[str] = frozenset(),
        func_names: frozenset[str] = frozenset(),
        func_sigs: dict | None = None,
        ffi_funcs: dict | None = None,
        imported_modules: dict | None = None,
        classes_sig: dict | None = None,
        global_types: dict[str, IRType] | None = None,
        global_list_el_ty: dict[str, str] | None = None,
        classes: list | None = None,
    ) -> None:
        self.data: list[IRGlobal] = []
        self.class_names = class_names
        self.func_names = func_names
        self.func_sigs = func_sigs or {}
        self.ffi_funcs = ffi_funcs or {}
        self.imported_modules = imported_modules or {}
        self.classes_sig = classes_sig or {}
        self.global_types = global_types or {}
        self.global_names = frozenset(self.global_types)
        self.global_list_el_ty = global_list_el_ty or {}
        self.class_ids: dict[str, int] = {
            name: i for i, name in enumerate(sorted(class_names))
        }
        # `class C: x = 5` (plain class, not @dataclass -- a dataclass's
        # class vars are per-instance fields, handled entirely differently)
        # static class-level variables: `ClassName.attr` reads/writes a
        # real dedicated global, one per (class, var) pair, initialized
        # from its default expression at module-init time. Mirrors
        # codegen.py's `class_var_labels`/`class_var_defaults` exactly
        # (`__cv_<Class>__<var>` label convention) -- this was previously
        # entirely unimplemented on this backend: `ClassName.attr` fell
        # through to the generic instance-attribute dict-lookup fallback,
        # which treated the class's raw RTTI id (a small integer, e.g. 0)
        # as if it were a real object pointer and dereferenced it,
        # crashing (confirmed via gdb: SIGSEGV reading near address 0).
        self.class_var_labels: dict[tuple[str, str], str] = {}
        self.class_var_defaults: list[tuple[str, "A.Expr"]] = []
        for cls in classes or []:
            if getattr(cls, "is_dataclass", False):
                continue
            for cv in getattr(cls, "class_vars", []) or []:
                cvname, _annot, cvdefault = cv
                if cvdefault is None:
                    continue
                label = f"__cv_{cls.name}__{cvname}"
                self.class_var_labels[(cls.name, cvname)] = label
                self.class_var_defaults.append((label, cvdefault))
        self._str_names: dict[str, str] = {}
        self._n = 0

    def intern_str(self, value: str) -> str:
        if value in self._str_names:
            return self._str_names[value]
        self._n += 1
        name = f"__str_{self._n}"
        self.data.append(IRGlobal(name=name, type=PTR, value=value))
        self._str_names[value] = name
        return name


class _FuncCtx:
    def __init__(
        self,
        mctx: _ModuleCtx,
        *,
        local_names: set[str] | None = None,
        declared_globals: set[str] | None = None,
        module_body: bool = False,
    ) -> None:
        self.mctx = mctx
        self.blocks: list[IRBlock] = []
        self.cur: IRBlock | None = None
        self.terminated = False
        self.slot: dict[str, IRValue] = {}  # var name -> alloca'd ptr
        self.slot_ty: dict[str, IRType] = {}  # var name -> value type in that slot
        # var name -> asmpython element type ("int"/"str") for a slot known
        # to hold a list -- there's no general element-type inference here
        # (unlike sema's own scope.list_el_types), just enough to type a
        # `for x in <list var>:` loop variable correctly for the common
        # case of a directly-assigned list literal. Defaults to "int".
        self.slot_el_ty: dict[str, str] = {}
        self.local_names = local_names or set()
        self.declared_globals = declared_globals or set()
        self.module_body = module_body
        # Names currently shadowed by an active comprehension's own loop
        # variable, tracked as a stack of sets (nested comprehensions push
        # their own on top). A comprehension variable is ALWAYS local, in
        # real Python, even at module scope (PEP 572 explicitly carves
        # comprehensions out as their own scope) -- but `_is_global_name`
        # below has no other way to know that, since `local_names` is
        # deliberately left empty at module scope (module_body=True), where
        # almost everything else really is a global. Checked before
        # `_is_global_name`'s normal logic; popped once the comprehension
        # finishes lowering. See `_lower_comprehension`/
        # `_lower_dict_comprehension`'s push/pop around the loop-var slot.
        self.comprehension_shadows: list[set[str]] = []
        self.loop_stack: list[tuple[str, str]] = []  # (continue_label, break_label)
        # Stack of slot names for active try-block parent-handler pointers.
        # Each entry is the `__try_parent_<uid>` slot name pushed when entering
        # a try body and popped when leaving. A `return` inside a try body must
        # restore `_runtime_handler_top` for every enclosing try before the ret.
        self.try_handler_stack: list[str] = []
        # (setjmp_block_index, end_block_index) per try/except this
        # function lowers -- populated by _lower_try, consumed by
        # lower_func to stamp IRFunc.try_regions for the x86-64 backend's
        # register allocator (see IRFunc.try_regions' own docstring for
        # why this exists).
        self.try_regions: list[tuple[int, int]] = []
        # One shared, function-entry-defined zero, reused everywhere a
        # discardable "return value" is needed (print()/list.append() etc.
        # are all expression-shaped but really void) -- set once by
        # lower_func right after the entry block exists. Sharing this one
        # value instead of minting+emitting a fresh never-read `const 0`
        # at every such call site matters: a value nothing ever reads is a
        # safe eviction candidate, but the backend has no way to write an
        # evicted *destination* register's value to memory (only spilled
        # *reads* are supported) -- so emitting dozens of these per
        # function used to make `_dst_gp` assert on whichever one got
        # evicted, even though none of them needed to exist as separate
        # values in the first place. Safe to read from any block: the
        # entry block unconditionally executes before everything else in
        # this architecture (no other function entry point).
        self.shared_zero: IRValue | None = None
        self._tmp = 0
        self._blk = 0

    def tmp(self, ty: IRType) -> IRValue:
        self._tmp += 1
        return IRValue(f"%t{self._tmp}", ty)

    def new_block(self, hint: str) -> IRBlock:
        self._blk += 1
        b = IRBlock(label=f"L{hint}{self._blk}")
        self.blocks.append(b)
        return b

    def switch_to(self, b: IRBlock) -> None:
        self.cur = b
        self.terminated = False

    def emit(self, instr: IRInstr) -> None:
        if self.terminated:
            return  # unreachable code after a terminator; drop it
        assert self.cur is not None
        self.cur.instrs.append(instr)
        if instr.op in ("ret", "br", "br.t"):
            self.terminated = True

    def ensure_slot(self, name: str, ty: IRType) -> IRValue:
        if name not in self.slot:
            ptr = self.tmp(IRType("ptr"))
            self.slot[name] = ptr
            self.slot_ty[name] = ty
            self.emit(IRInstr("alloca", ptr, []))
        return self.slot[name]

    def raw_slot(self, name: str, n_bytes: int) -> IRValue:
        """Reserve n_bytes of raw stack space (e.g. a jmp_buf), returning a PTR."""
        if name not in self.slot:
            ptr = self.tmp(PTR)
            self.slot[name] = ptr
            self.slot_ty[name] = PTR
            self.emit(IRInstr("alloca", ptr, [n_bytes]))
        return self.slot[name]


_BINOP = {
    "+": "iadd", "-": "isub", "*": "imul",
    "//": "idiv", "%": "irem",
    "&": "iand", "|": "ior", "^": "ixor",
    "<<": "shl", ">>": "shr",
}

_CMPOP = {
    "==": "icmp.eq", "!=": "icmp.ne",
    "is": "icmp.eq", "is not": "icmp.ne",
    "<": "icmp.lt", "<=": "icmp.le",
    ">": "icmp.gt", ">=": "icmp.ge",
}

# Only +/-/*/ true-division have a direct backend float op; floor-div/mod/
# bitwise on floats are rejected by sema already (see ast_nodes.expr_type's
# "bitwise ops only legal on ints" comment), so there's nothing to lower.
_FBINOP = {"+": "fadd", "-": "fsub", "*": "fmul", "/": "fdiv"}

_FCMPOP = {
    "==": "fcmp.eq", "!=": "fcmp.ne",
    "<": "fcmp.lt", "<=": "fcmp.le",
    ">": "fcmp.gt", ">=": "fcmp.ge",
}


# LIST_HEADER layout (mirrors codegen.py's Codegen.LIST_*_OFF exactly):
# cap@0, len@8, buf@16, all 8-byte fields.
_LIST_LEN_OFF = 8
_LIST_BUF_OFF = 16


def _list_elem_addr(ctx: _FuncCtx, list_v: IRValue, idx_v: IRValue) -> IRValue:
    """Address of list_v[idx_v], with Python-style negative-index
    wraparound and no bounds check -- matches codegen.py's own documented
    silent-corrupt-on-out-of-range behavior for list subscript (raising
    IndexError needs exception support, not added to this pipeline yet)."""
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [list_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))

    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    is_neg = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", is_neg, [idx_v, zero]))
    adj = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", adj, [is_neg, len_v]))  # is_neg is 0/1 -> len_v or 0
    real_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", real_idx, [idx_v, adj]))

    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [8]))
    byte_off = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", byte_off, [real_idx, eight]))

    buf_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", buf_addr, [list_v, _LIST_BUF_OFF]))
    buf_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", buf_v, [buf_addr]))

    elem_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", elem_addr, [buf_v, byte_off]))
    return elem_addr


def _lower_list_pop_front(ctx: _FuncCtx, e: A.MethodCall) -> IRValue:
    result_ty = F64 if A.expr_type(e) == "float" else I64
    obj_v = _lower_expr(ctx, e.obj)
    obj_ptr = ctx.ensure_slot(f"__pop0_obj_{id(e)}", PTR)
    res_ptr = ctx.ensure_slot(f"__pop0_res_{id(e)}", result_ty)
    ctx.emit(IRInstr("store", None, [obj_v, obj_ptr]))

    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    first_addr = _list_elem_addr(ctx, obj_v, zero)
    first_v = ctx.tmp(result_ty)
    ctx.emit(IRInstr("load", first_v, [first_addr]))
    ctx.emit(IRInstr("store", None, [first_v, res_ptr]))

    idx_ptr = ctx.ensure_slot(f"__pop0_idx_{id(e)}", I64)
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    ctx.emit(IRInstr("store", None, [one, idx_ptr]))

    head_b = ctx.new_block("pop0head")
    body_b = ctx.new_block("pop0body")
    cont_b = ctx.new_block("pop0cont")
    end_b = ctx.new_block("pop0end")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    cur_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_obj, [obj_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [cur_obj, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [cur_idx, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_obj, [obj_ptr]))
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    src_addr = _list_elem_addr(ctx, body_obj, body_idx)
    elem_v = ctx.tmp(result_ty)
    ctx.emit(IRInstr("load", elem_v, [src_addr]))
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    prev_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", prev_idx, [body_idx, one2]))
    dst_addr = _list_elem_addr(ctx, body_obj, prev_idx)
    ctx.emit(IRInstr("store", None, [elem_v, dst_addr]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    inc_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", inc_idx, [idx_ptr]))
    one3 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one3, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [inc_idx, one3]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    final_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_obj, [obj_ptr]))
    final_len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", final_len_addr, [final_obj, _LIST_LEN_OFF]))
    final_len = ctx.tmp(I64)
    ctx.emit(IRInstr("load", final_len, [final_len_addr]))
    one4 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one4, [1]))
    new_len = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", new_len, [final_len, one4]))
    ctx.emit(IRInstr("store", None, [new_len, final_len_addr]))
    result = ctx.tmp(result_ty)
    ctx.emit(IRInstr("load", result, [res_ptr]))
    return result


def _lower_membership(ctx: _FuncCtx, needle_e: A.Expr, hay_e: A.Expr, negate: bool) -> IRValue:
    hay_ty = A.expr_type(hay_e)
    if hay_ty in ("dict", "set"):
        hay_v = _lower_expr(ctx, hay_e)
        key_v = _lower_dict_key(ctx, needle_e)
        result = ctx.tmp(I64)
        ctx.emit(IRInstr("call", result, ["_abi_dict_contains", hay_v, key_v]))
        if negate:
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            inv = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", inv, [result, zero]))
            return inv
        return result
    if hay_ty not in ("list", "tuple"):
        raise LowerError(f"unsupported compare membership ({hay_ty})")
    needle_v = _lower_expr(ctx, needle_e)
    hay_v = _lower_expr(ctx, hay_e)
    needle_ptr = ctx.ensure_slot(f"__mem_needle_{id(needle_e)}_{id(hay_e)}", needle_v.type)
    hay_ptr = ctx.ensure_slot(f"__mem_hay_{id(needle_e)}_{id(hay_e)}", PTR)
    res_ptr = ctx.ensure_slot(f"__mem_res_{id(needle_e)}_{id(hay_e)}", I64)
    idx_ptr = ctx.ensure_slot(f"__mem_idx_{id(needle_e)}_{id(hay_e)}", I64)
    ctx.emit(IRInstr("store", None, [needle_v, needle_ptr]))
    ctx.emit(IRInstr("store", None, [hay_v, hay_ptr]))
    initial = ctx.tmp(I64)
    ctx.emit(IRInstr("const", initial, [1 if negate else 0]))
    ctx.emit(IRInstr("store", None, [initial, res_ptr]))
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    head_b = ctx.new_block("memhead")
    body_b = ctx.new_block("membody")
    found_b = ctx.new_block("memfound")
    cont_b = ctx.new_block("memcont")
    end_b = ctx.new_block("memend")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_hay = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_hay, [hay_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [cur_hay, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_hay = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_hay, [hay_ptr]))
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    elem_addr = _list_elem_addr(ctx, body_hay, body_idx)
    elem_v = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", elem_v, [elem_addr]))
    cur_needle = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", cur_needle, [needle_ptr]))
    needle_ty = A.expr_type(needle_e)
    hay_elem_ty = _iter_element_type(hay_e)
    hay_tuple_elem_tys = getattr(hay_e, "tuple_elem_types", [])
    eq_v = ctx.tmp(I64)
    if (
        needle_ty in ("str", "any")
        and (
            hay_elem_ty in ("str", "any")
            or "str" in hay_tuple_elem_tys
            or "any" in hay_tuple_elem_tys
        )
    ):
        ctx.emit(IRInstr("call", eq_v, ["_abi_str_eq", cur_needle, elem_v]))
    else:
        ctx.emit(IRInstr("icmp.eq", eq_v, [cur_needle, elem_v]))
    ctx.emit(IRInstr("br.t", None, [eq_v, found_b.label, cont_b.label]))

    ctx.switch_to(found_b)
    found_value = ctx.tmp(I64)
    ctx.emit(IRInstr("const", found_value, [0 if negate else 1]))
    ctx.emit(IRInstr("store", None, [found_value, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(cont_b)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    result = ctx.tmp(I64)
    ctx.emit(IRInstr("load", result, [res_ptr]))
    return result


def _lower_list_remove(ctx: _FuncCtx, e: A.MethodCall) -> IRValue:
    if len(e.args) != 1:
        raise LowerError("unsupported expr MethodCall (list.remove arity)")
    if A.expr_type(e.args[0]) == "float":
        raise LowerError("unsupported expr MethodCall (list.remove float)")
    obj_v = _lower_expr(ctx, e.obj)
    needle_v = _lower_expr(ctx, e.args[0])
    obj_ptr = ctx.ensure_slot(f"__remove_obj_{id(e)}", PTR)
    needle_ptr = ctx.ensure_slot(f"__remove_needle_{id(e)}", needle_v.type)
    idx_ptr = ctx.ensure_slot(f"__remove_idx_{id(e)}", I64)
    ctx.emit(IRInstr("store", None, [obj_v, obj_ptr]))
    ctx.emit(IRInstr("store", None, [needle_v, needle_ptr]))
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    find_head = ctx.new_block("removefindhead")
    find_body = ctx.new_block("removefindbody")
    find_cont = ctx.new_block("removefindcont")
    shift_head = ctx.new_block("removeshifthead")
    shift_body = ctx.new_block("removeshiftbody")
    shift_cont = ctx.new_block("removeshiftcont")
    shrink_b = ctx.new_block("removeshrink")
    end_b = ctx.new_block("removeend")
    ctx.emit(IRInstr("br", None, [find_head.label]))

    ctx.switch_to(find_head)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_obj, [obj_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [cur_obj, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, find_body.label, end_b.label]))

    ctx.switch_to(find_body)
    body_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_obj, [obj_ptr]))
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    elem_addr = _list_elem_addr(ctx, body_obj, body_idx)
    elem_v = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", elem_v, [elem_addr]))
    cur_needle = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", cur_needle, [needle_ptr]))
    eq_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", eq_v, [cur_needle, elem_v]))
    ctx.emit(IRInstr("br.t", None, [eq_v, shift_head.label, find_cont.label]))

    ctx.switch_to(find_cont)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [find_head.label]))

    ctx.switch_to(shift_head)
    sh_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", sh_idx, [idx_ptr]))
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    src_idx0 = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", src_idx0, [sh_idx, one2]))
    ctx.emit(IRInstr("store", None, [src_idx0, idx_ptr]))
    ctx.emit(IRInstr("br", None, [shift_cont.label]))

    ctx.switch_to(shift_cont)
    src_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", src_idx, [idx_ptr]))
    sh_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", sh_obj, [obj_ptr]))
    sh_len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", sh_len_addr, [sh_obj, _LIST_LEN_OFF]))
    sh_len = ctx.tmp(I64)
    ctx.emit(IRInstr("load", sh_len, [sh_len_addr]))
    sh_keep = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", sh_keep, [src_idx, sh_len]))
    ctx.emit(IRInstr("br.t", None, [sh_keep, shift_body.label, shrink_b.label]))

    ctx.switch_to(shift_body)
    body_obj2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_obj2, [obj_ptr]))
    body_src_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_src_idx, [idx_ptr]))
    src_addr = _list_elem_addr(ctx, body_obj2, body_src_idx)
    moved = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", moved, [src_addr]))
    one3 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one3, [1]))
    dst_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", dst_idx, [body_src_idx, one3]))
    dst_addr = _list_elem_addr(ctx, body_obj2, dst_idx)
    ctx.emit(IRInstr("store", None, [moved, dst_addr]))
    next_src_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_src_idx, [body_src_idx, one3]))
    ctx.emit(IRInstr("store", None, [next_src_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [shift_cont.label]))

    ctx.switch_to(shrink_b)
    final_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_obj, [obj_ptr]))
    final_len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", final_len_addr, [final_obj, _LIST_LEN_OFF]))
    final_len = ctx.tmp(I64)
    ctx.emit(IRInstr("load", final_len, [final_len_addr]))
    one4 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one4, [1]))
    new_len = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", new_len, [final_len, one4]))
    ctx.emit(IRInstr("store", None, [new_len, final_len_addr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    return ctx.shared_zero


def _lower_list_index(ctx: _FuncCtx, e: A.MethodCall) -> IRValue:
    if len(e.args) != 1:
        raise LowerError("unsupported expr MethodCall (list.index arity)")
    if A.expr_type(e.args[0]) == "float":
        raise LowerError("unsupported expr MethodCall (list.index float)")
    obj_v = _lower_expr(ctx, e.obj)
    needle_v = _lower_expr(ctx, e.args[0])
    obj_ptr = ctx.ensure_slot(f"__index_obj_{id(e)}", PTR)
    needle_ptr = ctx.ensure_slot(f"__index_needle_{id(e)}", needle_v.type)
    idx_ptr = ctx.ensure_slot(f"__index_idx_{id(e)}", I64)
    result_ptr = ctx.ensure_slot(f"__index_res_{id(e)}", I64)
    ctx.emit(IRInstr("store", None, [obj_v, obj_ptr]))
    ctx.emit(IRInstr("store", None, [needle_v, needle_ptr]))
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
    ctx.emit(IRInstr("store", None, [zero, result_ptr]))

    head_b = ctx.new_block("indexhead")
    body_b = ctx.new_block("indexbody")
    cont_b = ctx.new_block("indexcont")
    found_b = ctx.new_block("indexfound")
    miss_b = ctx.new_block("indexmiss")
    end_b = ctx.new_block("indexend")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_obj, [obj_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [cur_obj, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, miss_b.label]))

    ctx.switch_to(body_b)
    body_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_obj, [obj_ptr]))
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    elem_addr = _list_elem_addr(ctx, body_obj, body_idx)
    elem_v = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", elem_v, [elem_addr]))
    cur_needle = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", cur_needle, [needle_ptr]))
    eq_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", eq_v, [cur_needle, elem_v]))
    ctx.emit(IRInstr("br.t", None, [eq_v, found_b.label, cont_b.label]))

    ctx.switch_to(cont_b)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(found_b)
    found_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", found_idx, [idx_ptr]))
    ctx.emit(IRInstr("store", None, [found_idx, result_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(miss_b)
    msg_name = ctx.mctx.intern_str("value not in list")
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["ValueError"]]))
    ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    result = ctx.tmp(I64)
    ctx.emit(IRInstr("load", result, [result_ptr]))
    return result


def _lower_comprehension(ctx: _FuncCtx, e: A.Comprehension) -> IRValue:
    if e.extra_for_iters:
        raise LowerError("unsupported expr Comprehension (multiple for clauses)")
    iter_ty = A.expr_type(e.iter)
    if iter_ty == "any":
        iter_ty = "list"
    if iter_ty not in ("str", "list", "tuple"):
        raise LowerError(f"unsupported expr Comprehension (iter {iter_ty})")
    if A.expr_type(e.elt) == "float":
        raise LowerError("unsupported expr Comprehension (float element)")

    iter_v = _lower_expr(ctx, e.iter)
    iter_ptr = ctx.ensure_slot(f"__comp_iter_{id(e)}", ir_type_for(iter_ty))
    ctx.emit(IRInstr("store", None, [iter_v, iter_ptr]))

    if iter_ty == "str":
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", len_v, ["strlen", iter_v]))
    else:
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [iter_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))

    cap_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", cap_v, [1]))
    real_cap_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", real_cap_v, [len_v, cap_v]))
    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_new_list", real_cap_v]))
    out_ptr = ctx.ensure_slot(f"__comp_out_{id(e)}", PTR)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))

    idx_ptr = ctx.ensure_slot(f"__comp_idx_{id(e)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    head_b = ctx.new_block("comphead")
    body_b = ctx.new_block("compbody")
    append_b = ctx.new_block("compappend")
    cont_b = ctx.new_block("compcont")
    end_b = ctx.new_block("compend")

    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_iter_v = ctx.tmp(ir_type_for(iter_ty))
    ctx.emit(IRInstr("load", cur_iter_v, [iter_ptr]))
    if iter_ty == "str":
        cur_len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", cur_len_v, ["strlen", cur_iter_v]))
    else:
        cur_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", cur_len_addr, [cur_iter_v, _LIST_LEN_OFF]))
        cur_len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", cur_len_v, [cur_len_addr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, cur_len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_iter_v = ctx.tmp(ir_type_for(iter_ty))
    ctx.emit(IRInstr("load", body_iter_v, [iter_ptr]))
    body_idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx_v, [idx_ptr]))
    if iter_ty == "str":
        elem_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", elem_v, ["_abi_str_char_at", body_iter_v, body_idx_v]))
        var_ty = PTR
    else:
        elem_addr = _list_elem_addr(ctx, body_iter_v, body_idx_v)
        elem_kind = "any" if A.expr_type(e.iter) == "any" else (getattr(e.iter, "list_el_type", "int") or "int")
        var_ty = ir_type_for(elem_kind)
        elem_v = ctx.tmp(var_ty)
        ctx.emit(IRInstr("load", elem_v, [elem_addr]))
    shadow_names: set = set()
    if e.targets:
        # `[elt for a, b in xs]` (tuple-unpack) -- was completely
        # unhandled: this whole branch didn't exist, so `e.var` (always
        # "" for this shape, per the parser) was used as the slot name
        # unconditionally, silently storing the per-iteration tuple
        # element under an empty-string-named slot and leaving `a`/`b`
        # unbound inside `e.elt`/`e.cond` -- they then resolved through
        # whatever `_is_global_name` happened to find (a stale value or
        # an unrelated global), not the real per-iteration tuple slot.
        # Confirmed via `[k for k, v in d.items() if v >= 2]` printing
        # `['c', 'c', 'c']` (the LAST key, read three times) instead of
        # `['b', 'c']`. Mirrors A.For's identical tuple-target unpack
        # (see the list-For lowering above) -- elem_v here is the
        # per-iteration tuple/2-tuple value; each target reads one slot
        # out of it via _list_elem_addr (tuples share the list layout).
        elem_types = getattr(e.iter, "tuple_elem_types", [])
        for i, target in enumerate(e.targets):
            idx = ctx.tmp(I64)
            ctx.emit(IRInstr("const", idx, [i]))
            item_addr = _list_elem_addr(ctx, elem_v, idx)
            target_ty = elem_types[i] if i < len(elem_types) else "any"
            item_v = ctx.tmp(ir_type_for(target_ty))
            ctx.emit(IRInstr("load", item_v, [item_addr]))
            _store_loop_target(ctx, target, item_v, target_ty)
            if isinstance(target, str):
                shadow_names.add(target)
    else:
        var_ptr = ctx.ensure_slot(e.var, var_ty)
        ctx.emit(IRInstr("store", None, [elem_v, var_ptr]))
        shadow_names.add(e.var)
    # A comprehension's own loop variable(s) are ALWAYS local in real
    # Python, even at module scope (PEP 572 carves comprehensions out as
    # their own scope) -- `ensure_slot`/`_store_loop_target` above already
    # get this right (always allocate a local slot), but a bare reference
    # to the SAME name inside `e.cond`/`e.elt` would otherwise resolve as
    # the module global of the same name if one exists (`_is_global_name`
    # has no other way to know this particular `x` means the
    # comprehension's `x`, not the module's). Confirmed via a minimal
    # repro: `x = 7; xs = [x * 2 for x in [1,2,3]]` read the global `x`
    # (7) inside the comprehension body instead of the loop variable,
    # producing `[14, 14, 14]` instead of `[2, 4, 6]`.
    ctx.comprehension_shadows.append(shadow_names)
    try:
        if e.cond is not None:
            cond_v = _lower_truthy(ctx, e.cond)
            ctx.emit(IRInstr("br.t", None, [cond_v, append_b.label, cont_b.label]))
        else:
            ctx.emit(IRInstr("br", None, [append_b.label]))

        ctx.switch_to(append_b)
        cur_out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_out_v, [out_ptr]))
        item_v = _lower_expr(ctx, e.elt)
        ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out_v, item_v]))
        ctx.emit(IRInstr("br", None, [cont_b.label]))
    finally:
        ctx.comprehension_shadows.pop()

    ctx.switch_to(cont_b)
    inc_idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", inc_idx_v, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx_v, [inc_idx_v, one]))
    ctx.emit(IRInstr("store", None, [next_idx_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    final_out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out_v, [out_ptr]))
    return final_out_v


def _lower_dict_comprehension(ctx: _FuncCtx, e: A.DictComprehension) -> IRValue:
    if getattr(e, "extra_for_iters", []):
        raise LowerError("unsupported expr DictComprehension (multiple for clauses)")
    if A.expr_type(e.value) == "float":
        raise LowerError("unsupported expr DictComprehension (float value)")

    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_new_instance"]))
    out_ptr = ctx.ensure_slot(f"__dcomp_out_{id(e)}", PTR)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))

    idx_ptr = ctx.ensure_slot(f"__dcomp_idx_{id(e)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    if (
        isinstance(e.iter, A.Call)
        and e.iter.func == "enumerate"
        and len(e.iter.args) >= 1
        and e.targets
        and len(e.targets) == 2
    ):
        inner = e.iter.args[0]
        src_t = A.expr_type(inner)
        if src_t == "any":
            src_t = "list"
        if src_t not in ("str", "list", "tuple"):
            raise LowerError(f"unsupported expr DictComprehension (enumerate {src_t})")
        elem_ty = _iter_element_type(inner)
        iter_v = _lower_expr(ctx, inner)
        iter_ptr = ctx.ensure_slot(f"__dcomp_iter_{id(e)}", ir_type_for(src_t))
        ctx.emit(IRInstr("store", None, [iter_v, iter_ptr]))

        head_b = ctx.new_block("dcompenumhead")
        body_b = ctx.new_block("dcompenumbody")
        insert_b = ctx.new_block("dcompenuminsert")
        cont_b = ctx.new_block("dcompenumcont")
        end_b = ctx.new_block("dcompenumend")

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cur_iter_v = ctx.tmp(ir_type_for(src_t))
        ctx.emit(IRInstr("load", cur_iter_v, [iter_ptr]))
        if src_t == "str":
            len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("call", len_v, ["strlen", cur_iter_v]))
        else:
            len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", len_addr, [cur_iter_v, _LIST_LEN_OFF]))
            len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", len_v, [len_addr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        body_iter_v = ctx.tmp(ir_type_for(src_t))
        ctx.emit(IRInstr("load", body_iter_v, [iter_ptr]))
        body_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", body_idx_v, [idx_ptr]))
        _store_loop_target(ctx, e.targets[0], body_idx_v, "int")
        if src_t == "str":
            elem_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", elem_v, ["_abi_str_char_at", body_iter_v, body_idx_v]))
        else:
            addr = _list_elem_addr(ctx, body_iter_v, body_idx_v)
            elem_v = ctx.tmp(ir_type_for(elem_ty))
            ctx.emit(IRInstr("load", elem_v, [addr]))
        _store_loop_target(ctx, e.targets[1], elem_v, elem_ty)
        if e.cond is not None:
            keep_v = _lower_truthy(ctx, e.cond)
            ctx.emit(IRInstr("br.t", None, [keep_v, insert_b.label, cont_b.label]))
        else:
            ctx.emit(IRInstr("br", None, [insert_b.label]))

        ctx.switch_to(insert_b)
        cur_out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_out_v, [out_ptr]))
        key_v = _lower_dict_key(ctx, e.key)
        val_v = _lower_expr(ctx, e.value)
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", cur_out_v, key_v, val_v]))
        ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        inc_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", inc_idx_v, [idx_ptr]))
        one = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one, [1]))
        next_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx_v, [inc_idx_v, one]))
        ctx.emit(IRInstr("store", None, [next_idx_v, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        final_out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", final_out_v, [out_ptr]))
        return final_out_v

    iter_ty = A.expr_type(e.iter)
    if iter_ty == "any":
        iter_ty = "list"
    if iter_ty not in ("str", "list", "tuple"):
        raise LowerError(f"unsupported expr DictComprehension (iter {iter_ty})")

    iter_v = _lower_expr(ctx, e.iter)
    iter_ptr = ctx.ensure_slot(f"__dcomp_iter_{id(e)}", ir_type_for(iter_ty))
    ctx.emit(IRInstr("store", None, [iter_v, iter_ptr]))

    head_b = ctx.new_block("dcomphead")
    body_b = ctx.new_block("dcompbody")
    insert_b = ctx.new_block("dcompinsert")
    cont_b = ctx.new_block("dcompcont")
    end_b = ctx.new_block("dcompend")

    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_iter_v = ctx.tmp(ir_type_for(iter_ty))
    ctx.emit(IRInstr("load", cur_iter_v, [iter_ptr]))
    if iter_ty == "str":
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", len_v, ["strlen", cur_iter_v]))
    else:
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [cur_iter_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_iter_v = ctx.tmp(ir_type_for(iter_ty))
    ctx.emit(IRInstr("load", body_iter_v, [iter_ptr]))
    body_idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx_v, [idx_ptr]))
    if iter_ty == "str":
        elem_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", elem_v, ["_abi_str_char_at", body_iter_v, body_idx_v]))
        var_ty = "str"
    else:
        elem_addr = _list_elem_addr(ctx, body_iter_v, body_idx_v)
        var_ty = _iter_element_type(e.iter)
        elem_v = ctx.tmp(ir_type_for(var_ty))
        ctx.emit(IRInstr("load", elem_v, [elem_addr]))
    if e.targets:
        tuple_types = list(getattr(e.iter, "el_tuple_types", []))
        if not tuple_types:
            tuple_types = list(getattr(e.iter, "tuple_elem_types", []))
        for i, target in enumerate(e.targets):
            item_idx = ctx.tmp(I64)
            ctx.emit(IRInstr("const", item_idx, [i]))
            item_addr = _list_elem_addr(ctx, elem_v, item_idx)
            target_ty = tuple_types[i] if i < len(tuple_types) and tuple_types[i] else "any"
            item_v = ctx.tmp(ir_type_for(target_ty))
            ctx.emit(IRInstr("load", item_v, [item_addr]))
            _store_loop_target(ctx, target, item_v, target_ty)
    else:
        var_ptr = ctx.ensure_slot(e.var, ir_type_for(var_ty))
        ctx.emit(IRInstr("store", None, [elem_v, var_ptr]))
    if e.cond is not None:
        cond_v = _lower_truthy(ctx, e.cond)
        ctx.emit(IRInstr("br.t", None, [cond_v, insert_b.label, cont_b.label]))
    else:
        ctx.emit(IRInstr("br", None, [insert_b.label]))

    ctx.switch_to(insert_b)
    cur_out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_out_v, [out_ptr]))
    key_v = _lower_dict_key(ctx, e.key)
    val_v = _lower_expr(ctx, e.value)
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cur_out_v, key_v, val_v]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    inc_idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", inc_idx_v, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx_v, [inc_idx_v, one]))
    ctx.emit(IRInstr("store", None, [next_idx_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    final_out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out_v, [out_ptr]))
    return final_out_v


def _new_list_from_len(ctx: _FuncCtx, len_v: IRValue) -> IRValue:
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    cap_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", cap_v, [len_v, one]))
    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_new_list", cap_v]))
    return out_v


def _lower_zero_byte_list(ctx: _FuncCtx, len_e: A.Expr) -> IRValue:
    len_v = _lower_expr(ctx, len_e)
    out_v = _new_list_from_len(ctx, len_v)
    out_ptr = ctx.ensure_slot(f"__bytes_out_{id(len_e)}", PTR)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))

    idx_ptr = ctx.ensure_slot(f"__bytes_idx_{id(len_e)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    head_b = ctx.new_block("byteshead")
    body_b = ctx.new_block("bytesbody")
    cont_b = ctx.new_block("bytescont")
    end_b = ctx.new_block("bytesend")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    cur_out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_out, [out_ptr]))
    byte_zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", byte_zero, [0]))
    ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out, byte_zero]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    final_out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out, [out_ptr]))
    return final_out


def _lower_str_to_byte_list(ctx: _FuncCtx, str_e: A.Expr) -> IRValue:
    str_v = _lower_expr(ctx, str_e)
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", len_v, ["strlen", str_v]))
    out_v = _new_list_from_len(ctx, len_v)
    out_ptr = ctx.ensure_slot(f"__bytes_str_out_{id(str_e)}", PTR)
    str_ptr = ctx.ensure_slot(f"__bytes_str_src_{id(str_e)}", PTR)
    idx_ptr = ctx.ensure_slot(f"__bytes_str_idx_{id(str_e)}", I64)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))
    ctx.emit(IRInstr("store", None, [str_v, str_ptr]))
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    head_b = ctx.new_block("bytesstrhead")
    body_b = ctx.new_block("bytesstrbody")
    cont_b = ctx.new_block("bytesstrcont")
    end_b = ctx.new_block("bytesstrend")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_str = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_str, [str_ptr]))
    cur_len = ctx.tmp(I64)
    ctx.emit(IRInstr("call", cur_len, ["strlen", cur_str]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, cur_len]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_str = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_str, [str_ptr]))
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    ch = ctx.tmp(U8)
    ctx.emit(IRInstr("load8", ch, [body_str, body_idx]))
    byte_v = ctx.tmp(I64)
    ctx.emit(IRInstr("zext", byte_v, [ch]))
    cur_out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_out, [out_ptr]))
    ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out, byte_v]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    final_out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out, [out_ptr]))
    return final_out


def _lower_int_to_bytes(ctx: _FuncCtx, e: A.MethodCall) -> IRValue:
    if not e.args:
        raise LowerError("unsupported expr MethodCall (int.to_bytes missing length)")
    num_v = _lower_expr(ctx, e.obj)
    len_v = _lower_expr(ctx, e.args[0])
    out_v = _new_list_from_len(ctx, len_v)
    out_ptr = ctx.ensure_slot(f"__to_bytes_out_{id(e)}", PTR)
    num_ptr = ctx.ensure_slot(f"__to_bytes_num_{id(e)}", I64)
    len_ptr = ctx.ensure_slot(f"__to_bytes_len_{id(e)}", I64)
    idx_ptr = ctx.ensure_slot(f"__to_bytes_idx_{id(e)}", I64)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))
    ctx.emit(IRInstr("store", None, [num_v, num_ptr]))
    ctx.emit(IRInstr("store", None, [len_v, len_ptr]))
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
    big_endian = len(e.args) >= 2 and isinstance(e.args[1], A.StrLit) and e.args[1].value == "big"

    head_b = ctx.new_block("tobyteshead")
    body_b = ctx.new_block("tobytesbody")
    cont_b = ctx.new_block("tobytescont")
    end_b = ctx.new_block("tobytesend")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_len = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_len, [len_ptr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, cur_len]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    if big_endian:
        cur_len2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", cur_len2, [len_ptr]))
        one = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one, [1]))
        last_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("isub", last_idx, [cur_len2, one]))
        shift_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("isub", shift_idx, [last_idx, body_idx]))
    else:
        shift_idx = body_idx
    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [8]))
    shift_bits = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", shift_bits, [shift_idx, eight]))
    cur_num = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_num, [num_ptr]))
    shifted = ctx.tmp(I64)
    ctx.emit(IRInstr("shr", shifted, [cur_num, shift_bits]))
    mask = ctx.tmp(I64)
    ctx.emit(IRInstr("const", mask, [255]))
    byte_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", byte_v, [shifted, mask]))
    cur_out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_out, [out_ptr]))
    ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out, byte_v]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    cur_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    next_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one2]))
    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    final_out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out, [out_ptr]))
    return final_out


def _value_truthy(ctx: _FuncCtx, v: IRValue) -> IRValue:
    """Convert an already-lowered value to an I64 0/1-ish value suitable
    for `br.t` (which does a raw `test reg,reg; jnz` on a GP register).
    Float values must be converted first since they live in XMM and br.t
    can't read those directly; everything else (int, and -- same
    approximation the legacy codegen.py and this file's existing If/While
    lowering already make -- any heap pointer) passes through as a plain
    nonzero/zero GP test."""
    if v.type is F64:
        zero = ctx.tmp(F64)
        ctx.emit(IRInstr("const", zero, [0.0]))
        r = ctx.tmp(I64)
        ctx.emit(IRInstr("fcmp.ne", r, [v, zero]))
        return r
    return v


def _lower_truthy(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    """Lower `e` then convert its value to truthy I64 -- see
    `_value_truthy`. Use this (not `_value_truthy` directly) whenever `e`
    hasn't been lowered yet, so it's only evaluated once."""
    return _value_truthy(ctx, _lower_expr(ctx, e))


def _resolve_class_chain(ctx: _FuncCtx, name: str) -> list[str]:
    out: list[str] = []
    cur = name
    while cur is not None and cur not in out:
        out.append(cur)
        sig = ctx.mctx.classes_sig.get(cur)
        cur = sig.parent if sig is not None else None
    return out


def _resolved_method_is_static(ctx: _FuncCtx, class_name: str, method: str) -> bool:
    """True when `ClassName.method` resolves to a real @staticmethod --
    used at the `ClassName.method(...)` call site to decide whether to
    pass the usual implicit receiver arg (self/cls, via ctx.shared_zero)
    at all. A staticmethod has none in its actual Python signature."""
    owner = _resolve_method_owner(ctx, class_name, method)
    if owner is None:
        return False
    sig = ctx.mctx.classes_sig.get(owner)
    if sig is None:
        return False
    msig = sig.methods.get(method)
    if msig is None:
        return False
    return "staticmethod" in getattr(msig, "decorators", [])


def _resolve_method_owner(ctx: _FuncCtx, class_name: str, method: str) -> str | None:
    for cname in _resolve_class_chain(ctx, class_name):
        sig = ctx.mctx.classes_sig.get(cname)
        if sig is not None and method in sig.methods:
            return cname
    return None


def _virtual_dispatch_rows(ctx: _FuncCtx, class_name: str, method: str) -> list[tuple[int, str]]:
    """[(class_id, owner)] for every user class that is `class_name` or
    descends from it and resolves `method` somewhere on its chain --
    mirrors codegen.py's _virtual_dispatch_rows exactly. A method call on a
    `class_name`-typed receiver can bind statically only when every row
    shares one owner; with overrides in play the call must dispatch on the
    instance's runtime __class__ id instead, since the static type names
    the base but the receiver at runtime may be a subclass."""
    rows: list[tuple[int, str]] = []
    for cname, cid in ctx.mctx.class_ids.items():
        if class_name not in _resolve_class_chain(ctx, cname):
            continue
        owner = _resolve_method_owner(ctx, cname, method)
        if owner is not None:
            rows.append((cid, owner))
    return rows


def _subclass_ids(ctx: _FuncCtx, target: str) -> list[int]:
    ids: list[int] = []
    for name, cid in ctx.mctx.class_ids.items():
        cur = name
        seen: list[str] = []
        while cur and cur not in seen:
            if cur == target:
                ids.append(cid)
                break
            seen.append(cur)
            sig = ctx.mctx.classes_sig.get(cur)
            cur = sig.parent if sig is not None else None
    return ids


def _resolve_str_dunder(ctx: _FuncCtx, class_name: str, repr_first: bool = False) -> tuple[str, str] | None:
    methods = ("__repr__", "__str__") if repr_first else ("__str__", "__repr__")
    for method in methods:
        owner = _resolve_method_owner(ctx, class_name, method)
        if owner is not None:
            return owner, method
    return None


def _value_repr_kind(t: str) -> int:
    if t == "str":
        return 1
    if t == "float":
        return 2
    return 0


def _composite_repr_kind(t: str, inner: str) -> int:
    if t == "list":
        return 3 | (_value_repr_kind(inner) << 4)
    if t == "dict":
        return 4 | (_value_repr_kind(inner) << 4)
    if t == "tuple":
        return 5
    return _value_repr_kind(t)


def _list_repr_kind(e: A.Expr) -> int:
    el = getattr(e, "list_el_type", "int") or "int"
    inner = getattr(e, "list_el_value_type", "int") or "int"
    if isinstance(e, A.ListLit):
        el = e.el_type or "int"
        inner = getattr(e, "el_value_type", "int") or "int"
    return _composite_repr_kind(el, inner)


def _dict_value_repr_kind(e: A.Expr) -> int:
    vt = getattr(e, "value_type", "int") or "int"
    inner = getattr(e, "inner_value_type", "int") or "int"
    return _composite_repr_kind(vt, inner)


def _lower_dict_key(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    key_ty = A.expr_type(e)
    if key_ty in ("str", "any"):
        return _lower_expr(ctx, e)
    if key_ty == "int":
        key_v = _lower_expr(ctx, e)
        base = ctx.tmp(I64)
        ctx.emit(IRInstr("const", base, [10]))
        empty_name = ctx.mctx.intern_str("")
        empty_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_int_to_base", key_v, base, empty_v]))
        return out
    raise LowerError(f"unsupported dict key type {key_ty!r}")


def _collect_declared_globals(stmts: list, out: set[str]) -> None:
    for s in stmts:
        if isinstance(s, A.Global):
            out.update(s.names)
        elif isinstance(s, A.If):
            _collect_declared_globals(s.then, out)
            _collect_declared_globals(s.orelse, out)
        elif isinstance(s, A.While):
            _collect_declared_globals(s.body, out)
            _collect_declared_globals(s.orelse, out)
        elif isinstance(s, A.For):
            _collect_declared_globals(s.body, out)
            _collect_declared_globals(s.orelse, out)
        elif isinstance(s, A.With):
            _collect_declared_globals(s.body, out)
        elif isinstance(s, A.Try):
            _collect_declared_globals(s.body, out)
            _collect_declared_globals(s.handler, out)
            for _types, _bind, hbody in s.extra_handlers:
                _collect_declared_globals(hbody, out)
            _collect_declared_globals(s.else_body, out)
            _collect_declared_globals(s.finally_body, out)


def _walk_named_exprs(e) -> list:
    """Recursively find every `A.NamedExpr` (walrus `target := value`)
    reachable from expression `e`, including inside comprehensions/IfExp/
    boolops/etc. A walrus target binds in the *enclosing* scope (PEP 572),
    not the comprehension's own loop scope, so callers that classify names
    as module-global vs. local need to see these even though they aren't
    top-level `A.Assign` statements."""
    out: list = []
    if e is None:
        return out
    if isinstance(e, A.NamedExpr):
        out.append(e)
        out.extend(_walk_named_exprs(e.value))
        return out
    if isinstance(e, (A.BinOp,)):
        out.extend(_walk_named_exprs(e.left))
        out.extend(_walk_named_exprs(e.right))
    elif isinstance(e, A.UnaryOp):
        out.extend(_walk_named_exprs(e.operand))
    elif isinstance(e, A.BoolOp):
        out.extend(_walk_named_exprs(e.left))
        out.extend(_walk_named_exprs(e.right))
    elif isinstance(e, A.Compare):
        for o in e.operands:
            out.extend(_walk_named_exprs(o))
    elif isinstance(e, A.IfExp):
        out.extend(_walk_named_exprs(e.test))
        out.extend(_walk_named_exprs(e.body))
        out.extend(_walk_named_exprs(e.orelse))
    elif isinstance(e, A.Call):
        for a in e.args:
            out.extend(_walk_named_exprs(a))
    elif isinstance(e, A.MethodCall):
        out.extend(_walk_named_exprs(e.obj))
        for a in e.args:
            out.extend(_walk_named_exprs(a))
    elif isinstance(e, A.Attr):
        out.extend(_walk_named_exprs(e.obj))
    elif isinstance(e, A.Subscript):
        out.extend(_walk_named_exprs(e.obj))
        if isinstance(e.index, A.Slice):
            out.extend(_walk_named_exprs(e.index.start))
            out.extend(_walk_named_exprs(e.index.stop))
            out.extend(_walk_named_exprs(e.index.step))
        else:
            out.extend(_walk_named_exprs(e.index))
    elif isinstance(e, A.ListLit):
        for el in e.elems:
            out.extend(_walk_named_exprs(el))
    elif isinstance(e, A.TupleLit):
        for el in e.elems:
            out.extend(_walk_named_exprs(el))
    elif isinstance(e, A.SetLit):
        for el in e.elems:
            out.extend(_walk_named_exprs(el))
    elif isinstance(e, A.DictLit):
        for k, v in zip(e.keys, e.values):
            out.extend(_walk_named_exprs(k))
            out.extend(_walk_named_exprs(v))
    elif isinstance(e, A.FString):
        for seg in e.segments:
            if not isinstance(seg, str):
                out.extend(_walk_named_exprs(seg))
    elif isinstance(e, A.Comprehension):
        out.extend(_walk_named_exprs(e.iter))
        out.extend(_walk_named_exprs(e.cond))
        out.extend(_walk_named_exprs(e.elt))
    elif isinstance(e, A.DictComprehension):
        out.extend(_walk_named_exprs(e.iter))
        out.extend(_walk_named_exprs(e.cond))
        out.extend(_walk_named_exprs(e.key))
        out.extend(_walk_named_exprs(e.value))
    return out


def _register_named_expr_names(exprs: list, out: set[str]) -> None:
    for e in exprs:
        for ne in _walk_named_exprs(e):
            out.add(ne.target)


def _collect_bound_names(stmts: list, out: set[str]) -> None:
    for s in stmts:
        if isinstance(s, A.ExprStmt):
            _register_named_expr_names([s.expr], out)
        elif isinstance(s, A.Return):
            _register_named_expr_names([s.value], out)
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            _register_named_expr_names([s.value], out)
            out.add(s.target)
        elif isinstance(s, A.AugAssign):
            out.add(s.target)
        elif isinstance(s, A.MultiAssign):
            out.update(s.targets)
        elif isinstance(s, A.TupleAssign):
            for target in s.targets:
                if isinstance(target, A.Name):
                    out.add(target.name)
        elif isinstance(s, A.For):
            out.add(s.var)
            for target in s.targets:
                if isinstance(target, str):
                    out.add(target)
                elif isinstance(target, list):
                    for sub in target:
                        if isinstance(sub, str):
                            out.add(sub)
            _collect_bound_names(s.body, out)
            _collect_bound_names(s.orelse, out)
        elif isinstance(s, A.With):
            if s.name is not None:
                out.add(s.name)
            _collect_bound_names(s.body, out)
        elif isinstance(s, A.Try):
            if s.bind_name is not None:
                out.add(s.bind_name)
            _collect_bound_names(s.body, out)
            _collect_bound_names(s.handler, out)
            for _types, bind_name, hbody in s.extra_handlers:
                if bind_name is not None:
                    out.add(bind_name)
                _collect_bound_names(hbody, out)
            _collect_bound_names(s.else_body, out)
            _collect_bound_names(s.finally_body, out)
        elif isinstance(s, A.If):
            _register_named_expr_names([s.test], out)
            _collect_bound_names(s.then, out)
            _collect_bound_names(s.orelse, out)
        elif isinstance(s, A.While):
            _register_named_expr_names([s.test], out)
            _collect_bound_names(s.body, out)
            _collect_bound_names(s.orelse, out)


def _register_named_expr_globals(exprs: list, out: dict[str, IRType], list_el_ty: dict[str, str]) -> None:
    for e in exprs:
        for ne in _walk_named_exprs(e):
            out.setdefault(ne.target, ir_type_for(A.expr_type(ne.value)))
            if A.expr_type(ne.value) == "list":
                list_el_ty.setdefault(ne.target, getattr(ne.value, "list_el_type", "int"))


def _collect_module_globals(stmts: list, out: dict[str, IRType], list_el_ty: dict[str, str]) -> None:
    for s in stmts:
        if isinstance(s, A.ExprStmt):
            _register_named_expr_globals([s.expr], out, list_el_ty)
        elif isinstance(s, A.Return):
            _register_named_expr_globals([s.value], out, list_el_ty)
        if isinstance(s, A.Assign) and isinstance(s.target, str):
            _register_named_expr_globals([s.value], out, list_el_ty)
            out.setdefault(s.target, ir_type_for(A.expr_type(s.value)))
            if A.expr_type(s.value) == "list":
                list_el_ty.setdefault(s.target, getattr(s.value, "list_el_type", "int"))
        elif isinstance(s, A.ConstDecl):
            out.setdefault(s.name, ir_type_for(A.expr_type(s.value)))
            if A.expr_type(s.value) == "list":
                list_el_ty.setdefault(s.name, getattr(s.value, "list_el_type", "int"))
        elif isinstance(s, A.MultiAssign):
            ty = ir_type_for(A.expr_type(s.value))
            for target in s.targets:
                out.setdefault(target, ty)
        elif isinstance(s, A.TupleAssign):
            if len(s.values) == len(s.targets):
                for target, value in zip(s.targets, s.values):
                    if isinstance(target, A.Name):
                        out.setdefault(target.name, ir_type_for(A.expr_type(value)))
            elif len(s.values) == 1 and A.expr_type(s.values[0]) in ("list", "tuple"):
                rhs_t = A.expr_type(s.values[0])
                has_star = any(isinstance(t, A.StarTarget) for t in s.targets)
                # A starred unpack (`a, *rest, b = xs`) always sources from
                # a single homogeneous list -- every plain target shares
                # ONE element type (_iter_element_type), unlike a plain
                # tuple-unpack (`a, b = some_tuple`) where each target has
                # its own per-index type from tuple_elem_types. Using the
                # tuple-only source for a list RHS (the pre-existing bug
                # this comment is fixing) silently fell back to "any" for
                # every target, which _lower_expr_as_str then treats as an
                # opaque PTR-shaped value -- register-allocates and
                # marshals differently from the true I64 the value
                # actually is, corrupting multi-arg print() calls that mix
                # it with a genuinely-int-typed argument.
                # NOTE: intentionally NOT named list_el_ty -- that's this
                # function's own dict parameter (list_el_ty: dict[str,
                # str]), and Python has no block scoping, so reassigning
                # it here would shadow the parameter for the rest of the
                # function's remaining statements/recursive calls.
                # Confirmed as a real bug the hard way: a later plain
                # `A.Assign` list statement crashed with 'str' object has
                # no attribute 'setdefault' once this shadowed it.
                uniform_el_ty = _iter_element_type(s.values[0]) if (rhs_t == "list" or has_star) else None
                elem_types = getattr(s.values[0], "tuple_elem_types", [])
                for i, target in enumerate(s.targets):
                    if isinstance(target, A.StarTarget):
                        # `*rest` always binds a fresh list (the
                        # "leftover" slice) regardless of the source's own
                        # element type.
                        out.setdefault(target.name, PTR)
                        continue
                    if not isinstance(target, A.Name):
                        continue
                    if uniform_el_ty is not None:
                        elem_ty = uniform_el_ty
                    else:
                        elem_ty = elem_types[i] if i < len(elem_types) else "any"
                    out.setdefault(target.name, ir_type_for(elem_ty))
        elif isinstance(s, A.For):
            iter_ty = "any"
            if s.target_types:
                if s.targets:
                    for target, target_ty in zip(s.targets, s.target_types):
                        if isinstance(target, str):
                            out.setdefault(target, ir_type_for(target_ty))
                else:
                    iter_ty = s.target_types[0]
            else:
                iter_ty = _iter_element_type(s.iter)
            # A tuple-unpack for-loop (`for k, v in ...:`) carries its bound
            # names in s.targets, leaving s.var empty ('') -- registering
            # that empty string as a "global" corrupts the COFF symbol
            # table downstream (an unnamed external symbol the object
            # writer can't serialize). Only single-target loops use s.var.
            if not s.targets:
                out.setdefault(s.var, ir_type_for(iter_ty))
            _collect_module_globals(s.body, out, list_el_ty)
            _collect_module_globals(s.orelse, out, list_el_ty)
        elif isinstance(s, A.With):
            if s.name is not None:
                out.setdefault(s.name, ir_type_for(A.expr_type(s.expr)))
            _collect_module_globals(s.body, out, list_el_ty)
        elif isinstance(s, A.Try):
            if s.bind_name is not None:
                out.setdefault(s.bind_name, PTR)
            _collect_module_globals(s.body, out, list_el_ty)
            _collect_module_globals(s.handler, out, list_el_ty)
            for _types, bind_name, hbody in s.extra_handlers:
                if bind_name is not None:
                    out.setdefault(bind_name, PTR)
                _collect_module_globals(hbody, out, list_el_ty)
            _collect_module_globals(s.else_body, out, list_el_ty)
            _collect_module_globals(s.finally_body, out, list_el_ty)
        elif isinstance(s, A.If):
            _register_named_expr_globals([s.test], out, list_el_ty)
            _collect_module_globals(s.then, out, list_el_ty)
            _collect_module_globals(s.orelse, out, list_el_ty)
        elif isinstance(s, A.While):
            _register_named_expr_globals([s.test], out, list_el_ty)
            _collect_module_globals(s.body, out, list_el_ty)
            _collect_module_globals(s.orelse, out, list_el_ty)


def _is_global_name(ctx: _FuncCtx, name: str) -> bool:
    for shadow_set in ctx.comprehension_shadows:
        if name in shadow_set:
            return False
    if name in ctx.declared_globals:
        return True
    if ctx.module_body:
        return name in ctx.mctx.global_names
    if name in ctx.local_names:
        return False
    return name in ctx.mctx.global_names


def _name_ptr(ctx: _FuncCtx, name: str, ty: IRType) -> IRValue:
    if _is_global_name(ctx, name):
        ptr = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", ptr, [name]))
        return ptr
    return ctx.ensure_slot(name, ty)


def _iter_element_type(e: A.Expr) -> str:
    iter_t = A.expr_type(e)
    if iter_t == "dict":
        return "str"
    if iter_t == "any":
        return "any"
    if isinstance(e, A.ListLit):
        return e.el_type
    return getattr(e, "list_el_type", "int") or "int"


def _store_loop_target(ctx: _FuncCtx, target, value: IRValue, ty: str) -> None:
    if isinstance(target, str):
        ptr = _name_ptr(ctx, target, ir_type_for(ty))
        ctx.emit(IRInstr("store", None, [value, ptr]))
        return
    if isinstance(target, list):
        for i, sub in enumerate(target):
            idx = ctx.tmp(I64)
            ctx.emit(IRInstr("const", idx, [i]))
            addr = _list_elem_addr(ctx, value, idx)
            elem = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", elem, [addr]))
            _store_loop_target(ctx, sub, elem, "any")


def _store_tuple_assign_target(ctx: _FuncCtx, target: A.Expr, val: IRValue) -> None:
    """Store `val` (already evaluated) into a parallel-form TupleAssign
    target -- Name, Subscript (dict/list, non-slice), or Attr. Ports
    codegen.py's per-target dispatch in its TupleAssign parallel-form
    loop; `val` is pre-evaluated by the caller (all RHS values are
    evaluated before any store, so `a, b = b, a` swaps correctly)."""
    if isinstance(target, A.Name):
        ptr = _name_ptr(ctx, target.name, ctx.mctx.global_types.get(target.name, val.type))
        ctx.emit(IRInstr("store", None, [val, ptr]))
        return
    if isinstance(target, A.Subscript):
        obj_ty = A.expr_type(target.obj)
        if obj_ty == "dict":
            obj_v = _lower_expr(ctx, target.obj)
            key_v = _lower_dict_key(ctx, target.index)
            store_val = val
            if val.type is F64:
                iv = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
                store_val = iv
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_v, store_val]))
            return
        if obj_ty != "list":
            raise LowerError(f"unsupported stmt TupleAssign (Subscript target {obj_ty})")
        obj_v = _lower_expr(ctx, target.obj)
        idx_v = _lower_expr(ctx, target.index)
        addr = _list_elem_addr(ctx, obj_v, idx_v)
        ctx.emit(IRInstr("store", None, [val, addr]))
        return
    if isinstance(target, A.Attr):
        obj_v = _lower_expr(ctx, target.obj)
        name = ctx.mctx.intern_str(target.name)
        key_ptr = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", key_ptr, [name]))
        store_val = val
        if val.type is F64:
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
            store_val = iv
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_ptr, store_val]))
        return
    raise LowerError(f"unsupported stmt TupleAssign (target {type(target).__name__})")


def _lower_tuple_repr(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    """Tuple value -> repr string "(a, b, c)", matching codegen.py's
    _emit_tuple_repr_inline exactly (including the CPython 1-tuple
    trailing-comma special case, "(x,)"). Tuple slots are heterogeneous
    and their types are known at compile time, so this unrolls per
    element rather than looping like _abi_list_repr does for a
    uniformly-typed list."""
    kinds = A.tuple_element_types(e)
    if "float" in kinds:
        # _abi_fmt_elem expects a float element's raw bits pre-moved into
        # a GP register (the ad-hoc convention codegen.py's own inline
        # movq-to-rax uses) -- this pipeline's `call` op instead routes an
        # F64-typed IR value through an XMM argument register, an ABI
        # mismatch with no bitcast IR op yet to bridge it. Same
        # already-known gap as float list/dict elements elsewhere in this
        # file; reject cleanly rather than emit a call that reads garbage.
        raise LowerError("unsupported expr TupleLit repr (float element)")
    obj = _lower_expr(ctx, e)
    buf_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", buf_addr, [obj, _LIST_BUF_OFF]))
    buf_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", buf_v, [buf_addr]))

    lparen = ctx.mctx.intern_str("(")
    acc = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", acc, [lparen]))

    for i, k in enumerate(kinds):
        if i > 0:
            comma = ctx.mctx.intern_str(", ")
            comma_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", comma_v, [comma]))
            new_acc = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", new_acc, ["_abi_str_concat", acc, comma_v]))
            acc = new_acc
        elem_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", elem_addr, [buf_v, i * 8]))
        elem_v = ctx.tmp(F64 if k == "float" else I64)
        ctx.emit(IRInstr("load", elem_v, [elem_addr]))
        kind_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", kind_v, [_value_repr_kind(k)]))
        elem_repr = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", elem_repr, ["_abi_fmt_elem", elem_v, kind_v]))
        new_acc2 = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", new_acc2, ["_abi_str_concat", acc, elem_repr]))
        acc = new_acc2

    close_text = ",)" if len(kinds) == 1 else ")"
    close = ctx.mctx.intern_str(close_text)
    close_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", close_v, [close]))
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out, ["_abi_str_concat", acc, close_v]))
    return out


# ── f-string format-spec parsing (pure, compile-time; ports codegen.py's
# identically-named methods verbatim -- see that file's docstrings for the
# full rationale on each). fmt_spec is always a literal string captured at
# lex time (f"{x:{width}}"-style runtime specs aren't supported by either
# backend), so all of this is ordinary Python string manipulation
# producing compile-time constants, not IR.

def _cfmt_for_spec(spec: str, t: str) -> str | None:
    if not spec:
        return None
    if t == "float":
        if spec and spec[-1] in "feEgG":
            return "%" + spec
        return None
    if t == "int":
        if spec and spec[-1] in "dxXo":
            conv = spec[-1]
            flags = spec[:-1]
            return "%" + flags + "ll" + conv
        if spec.isdigit() or (spec.startswith("0") and spec[1:].isdigit()):
            return "%" + spec + "lld"
        return None
    return None


def _parse_binary_spec(body: str) -> tuple[int, bool] | None:
    if not body.endswith("b"):
        return None
    rest = body[:-1]
    if rest and rest[0] in "+- ":
        rest = rest[1:]
    prefix_flag = rest.startswith("#")
    if prefix_flag:
        rest = rest[1:]
    if rest and not rest.isdigit():
        return None
    return (int(rest) if rest else 0), prefix_flag


def _strip_grouping_option(spec: str) -> tuple[str | None, str]:
    if "," in spec:
        idx = spec.find(",")
        return ",", spec[:idx] + spec[idx + 1:]
    if "_" in spec:
        idx2 = spec.find("_")
        return "_", spec[:idx2] + spec[idx2 + 1:]
    return None, spec


def _split_fmt_align(spec: str) -> tuple[str, str | None, str]:
    if len(spec) >= 2 and spec[1] in "<>^=":
        return spec[0], spec[1], spec[2:]
    if len(spec) >= 1 and spec[0] in "<>^=":
        return " ", spec[0], spec[1:]
    return " ", None, spec


def _split_fmt_width(body: str, t: str) -> tuple[int | None, str]:
    if t == "str":
        i = 0
        while i < len(body) and body[i].isdigit():
            i += 1
        if i > 0 and body[i:] in ("", "s"):
            return int(body[:i]), ""
        return None, body
    i = 0
    while i < len(body) and body[i] in "+- #":
        i += 1
    prefix = body[:i]
    j = i
    if j < len(body) and body[j] == "0":
        j += 1
    k = j
    while k < len(body) and body[k].isdigit():
        k += 1
    if k == j:
        return None, body
    return int(body[j:k]), prefix + body[k:]


def _split_str_width_precision(body: str) -> tuple[int | None, int | None]:
    i = 0
    while i < len(body) and body[i].isdigit():
        i += 1
    width = int(body[:i]) if i > 0 else None
    j = i
    precision = None
    if j < len(body) and body[j] == ".":
        k = j + 1
        while k < len(body) and body[k].isdigit():
            k += 1
        if k > j + 1:
            precision = int(body[j + 1:k])
            j = k
    if j < len(body) and body[j] == "s":
        j += 1
    if j != len(body):
        return None, None
    return width, precision


def _lower_int_value_str(ctx: _FuncCtx, seg: A.Expr, rest: str) -> IRValue:
    """Evaluate int-typed `seg`, returning its formatted-string form (per
    the numeric format-spec `rest`, before any alignment/width padding) --
    ports codegen.py's _gen_int_value_str."""
    sep, rest = _strip_grouping_option(rest)
    binspec = _parse_binary_spec(rest) if rest else None
    if binspec is not None:
        width, prefix_flag = binspec
        n_v = _lower_expr(ctx, seg)
        width_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", width_v, [width]))
        pfx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", pfx_v, [1 if prefix_flag else 0]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_int_to_binary", n_v, width_v, pfx_v]))
        return out
    cfmt = _cfmt_for_spec(rest, "int") if rest else None
    n_v = _lower_expr(ctx, seg)
    if cfmt is not None:
        fmt_name = ctx.mctx.intern_str(cfmt)
        fmt_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", fmt_v, [fmt_name]))
        buf_name = ctx.mctx.intern_str("")
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_int_fmt", n_v, fmt_v]))
    else:
        base_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", base_v, [10]))
        empty_name = ctx.mctx.intern_str("")
        empty_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_int_to_base", n_v, base_v, empty_v]))
    if sep is not None:
        sep_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", sep_v, [ord(sep)]))
        grouped = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", grouped, ["_abi_group_digits", out, sep_v]))
        out = grouped
    return out


def _lower_fstring_aligned(
    ctx: _FuncCtx, seg: A.Expr, t: str, conv: str,
    width: int | None, fill: str, align: str, rest: str,
    precision: int | None,
) -> IRValue:
    """Ports codegen.py's _gen_fstring_aligned: evaluate `seg` to its
    (unpadded) string form, then pad/justify to `width` with `fill`."""
    if t == "str":
        val = _lower_expr_as_str(ctx, seg, repr_mode=conv in ("r", "a"))
        if precision is not None:
            prec_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", prec_v, [precision]))
            trunc = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", trunc, ["_abi_str_truncate", val, prec_v]))
            val = trunc
    elif t == "float":
        sep, rest2 = _strip_grouping_option(rest) if rest else (None, rest)
        cfmt = _cfmt_for_spec(rest2, "float") if rest2 else None
        f_v = _lower_expr(ctx, seg)
        if cfmt is not None:
            fmt_name = ctx.mctx.intern_str(cfmt)
            fmt_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", fmt_v, [fmt_name]))
            val = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", val, ["_abi_float_fmt", f_v, fmt_v]))
        else:
            val = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", val, ["_abi_float_to_str", f_v]))
        if sep is not None:
            sep_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", sep_v, [ord(sep)]))
            grouped = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", grouped, ["_abi_group_digits", val, sep_v]))
            val = grouped
    else:
        # A non-empty format spec on a bool/None formats the underlying
        # int value (0/1), not "True"/"False"/"None" -- matches CPython's
        # int.__format__ (bool has no __format__ override).
        val = _lower_int_value_str(ctx, seg, rest)

    helper = {"<": "_abi_str_ljust", ">": "_abi_str_rjust", "^": "_abi_str_center"}[align]
    fill_name = ctx.mctx.intern_str(fill if fill else " ")
    fill_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", fill_v, [fill_name]))
    width_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", width_v, [width if width is not None else 0]))
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out, [helper, val, width_v, fill_v]))
    return out


def _lower_fstring_segment(ctx: _FuncCtx, seg: A.Expr) -> IRValue:
    """Evaluate one f-string segment to a str value, honoring its
    fmt_spec/conv_flag (stamped by the parser -- see ast_nodes.py's
    FString docstring). Ports codegen.py's _gen_fstring_segment."""
    t = A.expr_type(seg)
    spec: str = getattr(seg, "fmt_spec", "")
    conv: str = getattr(seg, "conv_flag", "")
    if spec:
        fill, align, body = _split_fmt_align(spec)
        width: int | None = None
        rest = body
        precision: int | None = None
        if t == "str":
            width, precision = _split_str_width_precision(body)
            rest = ""
            if align is None and width is not None:
                fill, align = " ", "<"
        elif align is not None:
            width, rest = _split_fmt_width(body, t)
        if align in ("<", ">", "^") and t in ("str", "int", "float"):
            return _lower_fstring_aligned(ctx, seg, t, conv, width, fill, align, rest, precision)
        if t == "str" and precision is not None:
            val = _lower_expr_as_str(ctx, seg, repr_mode=conv in ("r", "a"))
            prec_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", prec_v, [precision]))
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_str_truncate", val, prec_v]))
            return out
        if t in ("int", "float"):
            sep, body2 = _strip_grouping_option(body)
            if t == "int":
                binspec = _parse_binary_spec(body2)
                if binspec is not None:
                    bwidth, prefix_flag = binspec
                    n_v = _lower_expr(ctx, seg)
                    width_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", width_v, [bwidth]))
                    pfx_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", pfx_v, [1 if prefix_flag else 0]))
                    out = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", out, ["_abi_int_to_binary", n_v, width_v, pfx_v]))
                    return out
            # Zero-pad width + grouping (`f"{n:015,}"`): zero-pad the
            # integer part so the *grouped* result reaches `zwidth` chars,
            # via _abi_group_digits_zeropad instead of a plain cfmt
            # zero-pad (which would double-count against the separators).
            zwidth = None
            if (
                sep is not None
                and len(body2) >= 2
                and body2[0] == "0"
                and body2[1].isdigit()
            ):
                zwidth, body2 = _split_fmt_width(body2, t)
            cfmt = _cfmt_for_spec(body2, t)
            if cfmt is not None or sep is not None:
                if t == "float":
                    v = _lower_expr(ctx, seg)
                    if cfmt is not None:
                        fmt_name = ctx.mctx.intern_str(cfmt)
                        fmt_v = ctx.tmp(PTR)
                        ctx.emit(IRInstr("global_addr", fmt_v, [fmt_name]))
                        out = ctx.tmp(PTR)
                        ctx.emit(IRInstr("call", out, ["_abi_float_fmt", v, fmt_v]))
                    else:
                        out = ctx.tmp(PTR)
                        ctx.emit(IRInstr("call", out, ["_abi_float_to_str", v]))
                else:
                    v = _lower_expr(ctx, seg)
                    if cfmt is not None:
                        fmt_name = ctx.mctx.intern_str(cfmt)
                        fmt_v = ctx.tmp(PTR)
                        ctx.emit(IRInstr("global_addr", fmt_v, [fmt_name]))
                        out = ctx.tmp(PTR)
                        ctx.emit(IRInstr("call", out, ["_abi_int_fmt", v, fmt_v]))
                    else:
                        base_v = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", base_v, [10]))
                        empty_name = ctx.mctx.intern_str("")
                        empty_v = ctx.tmp(PTR)
                        ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
                        out = ctx.tmp(PTR)
                        ctx.emit(IRInstr("call", out, ["_abi_int_to_base", v, base_v, empty_v]))
                if sep is not None:
                    sep_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", sep_v, [ord(sep)]))
                    if zwidth is not None:
                        zwidth_v = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", zwidth_v, [zwidth]))
                        grouped = ctx.tmp(PTR)
                        ctx.emit(IRInstr("call", grouped, ["_abi_group_digits_zeropad", out, zwidth_v, sep_v]))
                        out = grouped
                    else:
                        grouped = ctx.tmp(PTR)
                        ctx.emit(IRInstr("call", grouped, ["_abi_group_digits", out, sep_v]))
                        out = grouped
                # No dup-to-own-a-copy step needed here (unlike
                # codegen.py's _runtime_str_concat_dup after a cfmt hit):
                # _abi_int_fmt/_abi_float_fmt already malloc a fresh
                # buffer per call, unlike codegen.py's shared static
                # itoa_str_buf sprintf target.
                return out
    # Fallback: no spec, or a spec this dispatcher didn't handle above
    # (matches codegen.py falling through to the default str() path).
    return _lower_expr_as_str(ctx, seg, repr_mode=conv in ("r", "a"))


def _lower_expr_as_str(ctx: _FuncCtx, e: A.Expr, repr_mode: bool = False) -> IRValue:
    ty = A.expr_type(e)
    if ty == "str":
        s = _lower_expr(ctx, e)
        if not repr_mode:
            return s
        # repr(str) quote-wraps: matches codegen.py's repr() (`'` + text + `'`).
        q_name = ctx.mctx.intern_str("'")
        q = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", q, [q_name]))
        opened = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", opened, ["_abi_str_concat", q, s]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_str_concat", opened, q]))
        return out
    if ty == "int":
        if A.is_none_expr(e):
            name = ctx.mctx.intern_str("None")
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", out, [name]))
            return out
        if getattr(e, "dict_get_none_default", False):
            # `d.get(k)` (no explicit default): the runtime sentinel for
            # "key missing" is a plain 0 -- ambiguous at compile time with
            # a real int value of 0, so (unlike is_none_expr above) this
            # needs a runtime branch, not a static one. Mirrors
            # codegen.py's _emit_print_value handling of the same flag
            # (a runtime `test rax, rax` / print "None" if zero). Was
            # entirely unchecked here before this fix -- d.get("missing")
            # printed "0" instead of "None".
            n_v = _lower_expr(ctx, e)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            is_zero = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", is_zero, [n_v, zero]))
            none_b = ctx.new_block("dictgetnone")
            val_b = ctx.new_block("dictgetval")
            end_b = ctx.new_block("dictgetend")
            res_ptr = ctx.ensure_slot(f"__dictget_str_{id(e)}", PTR)
            ctx.emit(IRInstr("br.t", None, [is_zero, none_b.label, val_b.label]))
            ctx.switch_to(none_b)
            none_name = ctx.mctx.intern_str("None")
            none_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", none_v, [none_name]))
            ctx.emit(IRInstr("store", None, [none_v, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(val_b)
            base = ctx.tmp(I64)
            ctx.emit(IRInstr("const", base, [10]))
            prefix_name = ctx.mctx.intern_str("")
            prefix = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", prefix, [prefix_name]))
            val_str = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", val_str, ["_abi_int_to_base", n_v, base, prefix]))
            ctx.emit(IRInstr("store", None, [val_str, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(end_b)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out, [res_ptr]))
            return out
        if A.is_bool_expr(e):
            n_v = _lower_expr(ctx, e)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            is_zero = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", is_zero, [n_v, zero]))
            false_b = ctx.new_block("boolstrfalse")
            true_b = ctx.new_block("boolstrtrue")
            end_b = ctx.new_block("boolstrend")
            res_ptr = ctx.ensure_slot(f"__bool_str_{id(e)}", PTR)
            ctx.emit(IRInstr("br.t", None, [is_zero, false_b.label, true_b.label]))
            ctx.switch_to(false_b)
            false_name = ctx.mctx.intern_str("False")
            false_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", false_v, [false_name]))
            ctx.emit(IRInstr("store", None, [false_v, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(true_b)
            true_name = ctx.mctx.intern_str("True")
            true_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", true_v, [true_name]))
            ctx.emit(IRInstr("store", None, [true_v, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(end_b)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out, [res_ptr]))
            return out
        n = _lower_expr(ctx, e)
        base = ctx.tmp(I64)
        ctx.emit(IRInstr("const", base, [10]))
        prefix_name = ctx.mctx.intern_str("")
        prefix = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", prefix, [prefix_name]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_int_to_base", n, base, prefix]))
        return out
    if ty == "float":
        f_v = _lower_expr(ctx, e)
        if getattr(e, "dict_get_none_default", False):
            # Same "d.get(k) with no explicit default: 0 means key missing"
            # sentinel as the int branch above -- IEEE754 0.0 is all-zero
            # bits, so this is still a genuine zero test, just via fcmp
            # against a float 0.0 constant instead of an integer compare.
            zero = ctx.tmp(F64)
            ctx.emit(IRInstr("const", zero, [0.0]))
            is_zero = ctx.tmp(I64)
            ctx.emit(IRInstr("fcmp.eq", is_zero, [f_v, zero]))
            none_b = ctx.new_block("dictgetnonef")
            val_b = ctx.new_block("dictgetvalf")
            end_b = ctx.new_block("dictgetendf")
            res_ptr = ctx.ensure_slot(f"__dictget_strf_{id(e)}", PTR)
            ctx.emit(IRInstr("br.t", None, [is_zero, none_b.label, val_b.label]))
            ctx.switch_to(none_b)
            none_name = ctx.mctx.intern_str("None")
            none_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", none_v, [none_name]))
            ctx.emit(IRInstr("store", None, [none_v, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(val_b)
            val_str = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", val_str, ["_abi_float_to_str", f_v]))
            ctx.emit(IRInstr("store", None, [val_str, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(end_b)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out, [res_ptr]))
            return out
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_float_to_str", f_v]))
        return out
    if ty == "tuple":
        return _lower_tuple_repr(ctx, e)
    if ty == "list":
        obj = _lower_expr(ctx, e)
        kind = ctx.tmp(I64)
        ctx.emit(IRInstr("const", kind, [_list_repr_kind(e)]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_list_repr", obj, kind]))
        return out
    if ty == "dict":
        obj = _lower_expr(ctx, e)
        key_kind = ctx.tmp(I64)
        val_kind = ctx.tmp(I64)
        ctx.emit(IRInstr("const", key_kind, [1]))
        ctx.emit(IRInstr("const", val_kind, [_dict_value_repr_kind(e)]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_dict_repr", obj, key_kind, val_kind]))
        return out
    if ty == "set":
        obj = _lower_expr(ctx, e)
        kind = ctx.tmp(I64)
        ctx.emit(IRInstr("const", kind, [1]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_set_repr", obj, kind]))
        return out
    if ty.startswith("instance:"):
        cls_name = ty.split(":", 1)[1]
        resolved = _resolve_str_dunder(ctx, cls_name, repr_first=repr_mode)
        if resolved is not None:
            owner, method = resolved
            obj = _lower_expr(ctx, e)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, [f"{owner}__{method}", obj]))
            return out
        obj = _lower_expr(ctx, e)
        kind = ctx.tmp(I64)
        ctx.emit(IRInstr("const", kind, [0]))
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_fmt_elem", obj, kind]))
        return out
    if isinstance(e, A.FString):
        return _lower_fstring(ctx, e)
    val = _lower_expr(ctx, e)
    kind = ctx.tmp(I64)
    ctx.emit(IRInstr("const", kind, [0]))
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out, ["_abi_fmt_elem", val, kind]))
    return out


def _lower_type_name_attr(ctx: _FuncCtx, e: A.Attr) -> IRValue:
    arg = e.obj.args[0]
    arg_t = A.expr_type(arg)
    _lower_expr(ctx, arg)
    if arg_t == "int" and A.is_bool_expr(arg):
        name = "bool"
    elif arg_t == "int" and A.is_none_expr(arg):
        name = "NoneType"
    elif arg_t in ("int", "float", "str", "list", "dict", "tuple", "set"):
        name = arg_t
    elif arg_t.startswith("instance:"):
        name = arg_t.split(":", 1)[1]
    else:
        name = ""
    sym = ctx.mctx.intern_str(name)
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", out, [sym]))
    return out


def _lower_int_pow(ctx: _FuncCtx, base_v: IRValue, exp_v: IRValue, tag: int) -> IRValue:
    """base_v ** exp_v for two ints -- a non-negative-exponent multiply
    loop, matching codegen.py's pow() semantics exactly (no libc `pow`:
    that's double-only, and calling it with int args here would be the
    same GP/XMM ABI mismatch this whole file's other float-cell bitcast
    fixes exist to avoid). Shared by the pow() builtin and the "**"
    operator on two ints -- `tag` (typically id(the call/binop node))
    keeps each call site's loop-carried slots distinct."""
    res_ptr = ctx.ensure_slot(f"__pow_res_{tag}", I64)
    exp_ptr = ctx.ensure_slot(f"__pow_exp_{tag}", I64)
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    ctx.emit(IRInstr("store", None, [one, res_ptr]))
    ctx.emit(IRInstr("store", None, [exp_v, exp_ptr]))
    head_b = ctx.new_block("powhead")
    body_b = ctx.new_block("powbody")
    end_b = ctx.new_block("powend")
    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    cur_exp = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_exp, [exp_ptr]))
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.gt", keep_going, [cur_exp, zero]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))
    ctx.switch_to(body_b)
    cur_res = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_res, [res_ptr]))
    next_res = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", next_res, [cur_res, base_v]))
    ctx.emit(IRInstr("store", None, [next_res, res_ptr]))
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    next_exp = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", next_exp, [cur_exp, one2]))
    ctx.emit(IRInstr("store", None, [next_exp, exp_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(end_b)
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out, [res_ptr]))
    return out


def _emit_int_divzero_check(ctx: _FuncCtx, divisor: IRValue, tag: int) -> None:
    """Raise ZeroDivisionError("division by zero") if `divisor` is 0,
    matching CPython's message text (codegen.py's own
    `_runtime_zerodiv_msg`) -- otherwise a zero divisor reaches the raw
    x86 `idiv`/`irem` IR ops directly, which fault with SIGFPE at the
    hardware level: an uncatchable hard crash instead of a normal
    Python-level exception a `try`/`except ZeroDivisionError` can catch.
    Confirmed via gdb (`idiv %r13` faulting with r13=0) on a `divide(a,
    b): return a // b` call site with b=0 -- this backend's `idiv`/`irem`
    previously had NO zero-check at all, unlike codegen.py's inline jcc
    before every division."""
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    nonzero = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.ne", nonzero, [divisor, zero]))
    ok_b = ctx.new_block(f"divzero_ok_{tag}")
    raise_b = ctx.new_block(f"divzero_raise_{tag}")
    ctx.emit(IRInstr("br.t", None, [nonzero, ok_b.label, raise_b.label]))

    ctx.switch_to(raise_b)
    msg_name = ctx.mctx.intern_str("ZeroDivisionError: division by zero")
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["ZeroDivisionError"]]))
    ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
    ctx.emit(IRInstr("br", None, [ok_b.label]))

    ctx.switch_to(ok_b)


def _lower_int_floordivmod(ctx: _FuncCtx, a: IRValue, b: IRValue, want: str, tag: int) -> IRValue:
    """a // b or a % b for two ints, `want` selects which -- x86's IDIV
    (the IR "idiv"/"irem" ops) truncates toward zero, but Python's // and
    % floor toward -inf, so when the (nonzero) remainder's sign differs
    from the divisor's, correct: quotient -= 1, remainder += divisor.
    Matches codegen.py's inline correction (and _runtime_divmod's
    identical one) exactly, just as IR blocks instead of jcc chains --
    same reasoning as this file's other codegen.py ports (see
    _virtual_dispatch_rows/_lower_int_pow)."""
    _emit_int_divzero_check(ctx, b, tag)
    raw_q = ctx.tmp(I64)
    ctx.emit(IRInstr("idiv", raw_q, [a, b]))
    raw_r = ctx.tmp(I64)
    ctx.emit(IRInstr("irem", raw_r, [a, b]))

    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    r_nonzero = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.ne", r_nonzero, [raw_r, zero]))

    check_b = ctx.new_block("floordivmodcheck")
    fix_b = ctx.new_block("floordivmodfix")
    end_b = ctx.new_block("floordivmodend")
    q_ptr = ctx.ensure_slot(f"__floordiv_q_{tag}", I64)
    r_ptr = ctx.ensure_slot(f"__floordiv_r_{tag}", I64)
    ctx.emit(IRInstr("store", None, [raw_q, q_ptr]))
    ctx.emit(IRInstr("store", None, [raw_r, r_ptr]))
    ctx.emit(IRInstr("br.t", None, [r_nonzero, check_b.label, end_b.label]))

    ctx.switch_to(check_b)
    signs_xor = ctx.tmp(I64)
    ctx.emit(IRInstr("ixor", signs_xor, [raw_r, b]))
    diff_sign = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", diff_sign, [signs_xor, zero]))
    ctx.emit(IRInstr("br.t", None, [diff_sign, fix_b.label, end_b.label]))

    ctx.switch_to(fix_b)
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    fixed_q = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", fixed_q, [raw_q, one]))
    fixed_r = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", fixed_r, [raw_r, b]))
    ctx.emit(IRInstr("store", None, [fixed_q, q_ptr]))
    ctx.emit(IRInstr("store", None, [fixed_r, r_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out, [q_ptr if want == "//" else r_ptr]))
    return out


def _lower_set_setop(ctx: _FuncCtx, obj_e: A.Expr, other_e: A.Expr, method: str, tag: int) -> IRValue:
    """s.union(o) / s.intersection(o) / s.difference(o) -- ports
    codegen.py's _gen_set_setop exactly. union is a fresh set with both
    operands merged in (right wins on conflicts, though sets have no
    payload so that's moot); intersection/difference iterate self's keys,
    keeping (intersection) or dropping (difference) each one based on its
    membership in other."""
    obj_v = _lower_expr(ctx, obj_e)
    other_v = _lower_expr(ctx, other_e)
    new_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", new_v, ["_abi_new_instance"]))

    if method == "union":
        ctx.emit(IRInstr("call", None, ["_abi_dict_update", new_v, obj_v]))
        ctx.emit(IRInstr("call", None, ["_abi_dict_update", new_v, other_v]))
        return new_v

    keys_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", obj_v]))
    keys_ptr = ctx.ensure_slot(f"__setop_keys_{tag}", PTR)
    ctx.emit(IRInstr("store", None, [keys_v, keys_ptr]))
    idx_ptr = ctx.ensure_slot(f"__setop_idx_{tag}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    head_b = ctx.new_block("setopheread")
    body_b = ctx.new_block("setopbody")
    keep_b = ctx.new_block("setopkeep")
    cont_b = ctx.new_block("setopcont")
    end_b = ctx.new_block("setopend")

    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [keys_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    cond = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", cond, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [cond, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    key_addr = _list_elem_addr(ctx, keys_v, idx_v)
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", key_v, [key_addr]))
    has_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", other_v, key_v]))
    zero2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero2, [0]))
    if method == "intersection":
        should_keep = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.ne", should_keep, [has_v, zero2]))
    else:
        should_keep = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", should_keep, [has_v, zero2]))
    ctx.emit(IRInstr("br.t", None, [should_keep, keep_b.label, cont_b.label]))

    ctx.switch_to(keep_b)
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", new_v, key_v, one_v]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    inc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", inc_v, [idx_ptr]))
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    next_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_v, [inc_v, one2]))
    ctx.emit(IRInstr("store", None, [next_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    return new_v


def _lower_isinstance(ctx: _FuncCtx, e: A.Call) -> IRValue:
    targets: list[str] = []
    cls_arg = e.args[1]
    if isinstance(cls_arg, A.Name):
        targets.append(cls_arg.name)
    elif isinstance(cls_arg, A.Attr):
        targets.append(cls_arg.name)
    elif isinstance(cls_arg, A.TupleLit):
        for el in cls_arg.elems:
            if isinstance(el, A.Name):
                targets.append(el.name)
            elif isinstance(el, A.Attr):
                targets.append(el.name)

    prim_map = {
        "int": ("int",),
        "str": ("str",),
        "float": ("float",),
        "bool": ("int",),
        "list": ("list",),
        "dict": ("dict",),
        "tuple": ("tuple",),
        "set": ("set",),
    }
    arg0 = e.args[0]
    arg0_t = A.expr_type(arg0)
    has_prim_target = False
    prim_match = False
    for t in targets:
        if t in prim_map:
            has_prim_target = True
            if arg0_t in prim_map[t]:
                prim_match = True
            if t == "int" and A.is_bool_expr(arg0):
                prim_match = True
            if t == "bool" and A.is_bool_expr(arg0):
                prim_match = True
    if has_prim_target:
        _lower_expr(ctx, arg0)
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("const", out, [1 if prim_match else 0]))
        return out

    accept: list[int] = []
    for t in targets:
        for cid in _subclass_ids(ctx, t):
            if cid not in accept:
                accept.append(cid)

    obj_v = _lower_expr(ctx, arg0)
    zero = ctx.tmp(PTR if obj_v.type == PTR else I64)
    ctx.emit(IRInstr("const", zero, [0]))
    out_ptr = ctx.ensure_slot(f"__isinst_out_{id(e)}", I64)
    none_b = ctx.new_block("isinstnone")
    live_b = ctx.new_block("isinstlive")
    end_b = ctx.new_block("isinstend")
    is_none = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_none, [obj_v, zero]))
    ctx.emit(IRInstr("br.t", None, [is_none, none_b.label, live_b.label]))

    ctx.switch_to(none_b)
    none_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", none_v, [0]))
    ctx.emit(IRInstr("store", None, [none_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(live_b)
    key_sym = ctx.mctx.intern_str("__class__")
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", key_v, [key_sym]))
    miss_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", miss_v, [-1]))
    class_id = ctx.tmp(I64)
    ctx.emit(IRInstr("call", class_id, ["_abi_dict_get_default", obj_v, key_v, miss_v]))
    match_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", match_v, [0]))
    for cid in accept:
        cid_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", cid_v, [cid]))
        eq_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", eq_v, [class_id, cid_v]))
        next_v = ctx.tmp(I64)
        ctx.emit(IRInstr("ior", next_v, [match_v, eq_v]))
        match_v = next_v
    ctx.emit(IRInstr("store", None, [match_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out, [out_ptr]))
    return out


def _lower_sort_inplace(ctx: _FuncCtx, e, out_v: IRValue, el_kind: str) -> IRValue:
    """Sort `out_v` (a real list value, already the caller's to mutate --
    `_lower_sorted` passes a fresh clone, `list.sort()` passes the
    original list directly) according to `e`'s sort_key/sort_reverse
    attributes (stamped by sema's _check_sort_kwargs, shared by
    sorted()/list.sort()/min()/max()). Returns out_v unchanged (the
    caller decides what to do with it -- sorted() returns it, list.sort()
    discards it since Python's sort() returns None)."""
    sort_key = getattr(e, "sort_key", None)
    if sort_key is not None:
        if not isinstance(sort_key, A.Lambda) or len(sort_key.params) != 1:
            raise LowerError("unsupported expr Call (sorted key)")
        param = sort_key.params[0]
        key_body = sort_key.body
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [out_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        keys_v = _new_list_from_len(ctx, len_v)
        elems_ptr = ctx.ensure_slot(f"__sorted_elems_{id(e)}", PTR)
        keys_ptr = ctx.ensure_slot(f"__sorted_keys_{id(e)}", PTR)
        idx_ptr = ctx.ensure_slot(f"__sorted_idx_{id(e)}", I64)
        ctx.emit(IRInstr("store", None, [out_v, elems_ptr]))
        ctx.emit(IRInstr("store", None, [keys_v, keys_ptr]))
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
        head_b = ctx.new_block("sortedkeyhead")
        body_b = ctx.new_block("sortedkeybody")
        cont_b = ctx.new_block("sortedkeycont")
        end_b = ctx.new_block("sortedkeyend")
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cur_elems = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_elems, [elems_ptr]))
        cur_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", cur_len_addr, [cur_elems, _LIST_LEN_OFF]))
        cur_len = ctx.tmp(I64)
        ctx.emit(IRInstr("load", cur_len, [cur_len_addr]))
        keep_going = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, cur_len]))
        ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        body_elems = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", body_elems, [elems_ptr]))
        body_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
        item_addr = _list_elem_addr(ctx, body_elems, body_idx)
        item_v = ctx.tmp(PTR if el_kind not in ("int", "float") else ir_type_for(el_kind))
        ctx.emit(IRInstr("load", item_v, [item_addr]))
        if isinstance(key_body, A.Name) and key_body.name == param:
            key_v = item_v
        elif (
            isinstance(key_body, A.Subscript)
            and isinstance(key_body.obj, A.Name)
            and key_body.obj.name == param
            and isinstance(key_body.index, A.IntLit)
        ):
            key_idx = ctx.tmp(I64)
            ctx.emit(IRInstr("const", key_idx, [int(key_body.index.value)]))
            key_addr = _list_elem_addr(ctx, item_v, key_idx)
            key_v = ctx.tmp(ir_type_for(A.expr_type(key_body)))
            ctx.emit(IRInstr("load", key_v, [key_addr]))
        else:
            raise LowerError("unsupported expr Call (sorted key lambda body)")
        cur_keys = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_keys, [keys_ptr]))
        ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_keys, key_v]))
        ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        cur_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
        one = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one, [1]))
        next_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
        ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        elems_final = ctx.tmp(PTR)
        keys_final = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", elems_final, [elems_ptr]))
        ctx.emit(IRInstr("load", keys_final, [keys_ptr]))
        sort_sym = "_abi_sort_pairs_str" if getattr(e, "sort_key_ret", "int") == "str" else "_abi_sort_pairs_int"
        ctx.emit(IRInstr("call", None, [sort_sym, elems_final, keys_final]))
        out_v = elems_final
    else:
        if el_kind == "str":
            ctx.emit(IRInstr("call", None, ["_abi_sort_str", out_v]))
        elif el_kind == "tuple":
            ctx.emit(IRInstr("call", None, ["_abi_sort_items", out_v]))
        else:
            ctx.emit(IRInstr("call", None, ["_abi_sort_int", out_v]))

    if getattr(e, "sort_reverse", None) is not None:
        do_reverse = _lower_truthy(ctx, e.sort_reverse)
        rev_b = ctx.new_block("sortedrev")
        done_b = ctx.new_block("sortedrevdone")
        ctx.emit(IRInstr("br.t", None, [do_reverse, rev_b.label, done_b.label]))
        ctx.switch_to(rev_b)
        ctx.emit(IRInstr("call", None, ["_abi_list_reverse", out_v]))
        ctx.emit(IRInstr("br", None, [done_b.label]))
        ctx.switch_to(done_b)
    return out_v


def _lower_sorted(ctx: _FuncCtx, e: A.Call) -> IRValue:
    arg = e.args[0]
    arg_t = A.expr_type(arg)
    if arg_t in ("set", "dict"):
        src_v = _lower_expr(ctx, arg)
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v, ["_abi_dict_keys", src_v]))
        el_kind = "str"
    else:
        src_v = _lower_expr(ctx, arg)
        start_v = ctx.tmp(I64)
        stop_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", start_v, [-9223372036854775808]))
        ctx.emit(IRInstr("const", stop_v, [9223372036854775807]))
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v, ["_abi_list_slice", src_v, start_v, stop_v]))
        if isinstance(arg, A.Name):
            el_kind = getattr(arg, "list_el_type", "int") or "int"
        elif isinstance(arg, A.ListLit):
            el_kind = arg.el_type or "int"
        else:
            el_kind = getattr(arg, "list_el_type", "int") or "int"
    return _lower_sort_inplace(ctx, e, out_v, el_kind)


def _lower_fstring(ctx: _FuncCtx, e: A.FString) -> IRValue:
    if not e.segments:
        empty = ctx.mctx.intern_str("")
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", out, [empty]))
        return out
    # _lower_fstring_segment (not the plain default-str-conversion
    # _lower_expr_as_str) so each segment's fmt_spec/conv_flag (stamped
    # by the parser -- see FString's docstring in ast_nodes.py) actually
    # takes effect. A literal StrLit chunk has neither attr, so it falls
    # straight through to the same plain-str path either function would
    # take -- safe to route uniformly.
    acc = _lower_fstring_segment(ctx, e.segments[0])
    for seg in e.segments[1:]:
        rhs = _lower_fstring_segment(ctx, seg)
        joined = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", joined, ["_abi_str_concat", acc, rhs]))
        acc = joined
    return acc


def _lower_pct_format(ctx: _FuncCtx, e: A.BinOp) -> IRValue:
    """`"...%s/%d/%f..." % (args)` -- ports codegen.py's
    _gen_str_pct_format. e.left is sema-validated to be a literal format
    string; A.parse_pct_format is the single shared parser sema and this
    both use, so they can't drift out of sync. `%s`/`%r` reuse
    _lower_fstring_segment (stamping conv_flag="r" onto the arg node
    in-place for %r, same trick codegen.py's version uses) plus
    _abi_str_ljust/rjust for a width; %d/%i/%u/%o/%x/%X and %e/%E/%f/%F/
    %g/%G go through _abi_int_fmt/_abi_float_fmt with a printf format
    built from the flags/width/precision, translating Python's int
    conversions to the ll-sized C equivalents."""
    assert isinstance(e.left, A.StrLit)
    pieces, _ = A.parse_pct_format(e.left.value)
    args: list = e.right.elems if isinstance(e.right, A.TupleLit) else [e.right]
    arg_pos = 0

    def lower_piece(piece: tuple) -> IRValue:
        nonlocal arg_pos
        if piece[0] == "lit":
            name = ctx.mctx.intern_str(piece[1])
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", v, [name]))
            return v
        _, flags, width, precision, conv = piece
        arg = args[arg_pos]
        arg_pos += 1
        if conv in ("s", "r"):
            if conv == "r":
                arg.conv_flag = "r"  # type: ignore[attr-defined]
            val = _lower_fstring_segment(ctx, arg)
            if width:
                w_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", w_v, [int(width)]))
                fill_name = ctx.mctx.intern_str(" ")
                fill_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", fill_v, [fill_name]))
                helper = "_abi_str_ljust" if "-" in flags else "_abi_str_rjust"
                out = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", out, [helper, val, w_v, fill_v]))
                return out
            return val
        if conv in "diouxX":
            cconv = {"i": "d", "u": "d", "d": "d", "o": "o", "x": "x", "X": "X"}[conv]
            cfmt = "%" + flags + width + precision + "ll" + cconv
            fmt_name = ctx.mctx.intern_str(cfmt)
            fmt_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", fmt_v, [fmt_name]))
            n_v = _lower_expr(ctx, arg)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_int_fmt", n_v, fmt_v]))
            return out
        # eEfFgG: Python defaults precision to 6, same as C, when omitted.
        prec = precision if precision else ".6"
        cfmt = "%" + flags + width + prec + conv
        fmt_name = ctx.mctx.intern_str(cfmt)
        fmt_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", fmt_v, [fmt_name]))
        arg_ty = A.expr_type(arg)
        f_v = _lower_expr(ctx, arg)
        if arg_ty != "float":
            fv2 = ctx.tmp(F64)
            ctx.emit(IRInstr("sitofp", fv2, [f_v]))
            f_v = fv2
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out, ["_abi_float_fmt", f_v, fmt_v]))
        return out

    if not pieces:
        empty = ctx.mctx.intern_str("")
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", out, [empty]))
        return out
    acc = lower_piece(pieces[0])
    for piece in pieces[1:]:
        rhs = lower_piece(piece)
        joined = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", joined, ["_abi_str_concat", acc, rhs]))
        acc = joined
    return acc


def _emit_instance_field_set(ctx: _FuncCtx, obj_v: IRValue, name: str, val_v: IRValue) -> None:
    key_name = ctx.mctx.intern_str(name)
    key_ptr = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", key_ptr, [key_name]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_ptr, val_v]))


def _emit_list_from_exprs(ctx: _FuncCtx, exprs: list[A.Expr]) -> IRValue:
    cap_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", cap_v, [max(len(exprs), 1)]))
    list_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", list_v, ["_abi_new_list", cap_v]))
    for expr in exprs:
        item_v = _lower_expr(ctx, expr)
        ctx.emit(IRInstr("call", None, ["_abi_list_append", list_v, item_v]))
    return list_v


def _emit_dict_from_literal(ctx: _FuncCtx, expr: A.Expr) -> IRValue:
    dict_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", dict_v, ["_abi_new_instance"]))
    if A.is_none_expr(expr):
        return dict_v
    if not isinstance(expr, A.DictLit):
        raise LowerError("unsupported thread kwargs container")
    for key_e, val_e in zip(expr.keys, expr.values):
        if not isinstance(key_e, A.StrLit):
            raise LowerError("thread kwargs keys must be string literals")
        _emit_instance_field_set(ctx, dict_v, key_e.value, _lower_expr(ctx, val_e))
    return dict_v


def _literal_call_args_list(expr: A.Expr) -> list[A.Expr]:
    if A.is_none_expr(expr):
        return []
    if isinstance(expr, A.ListLit):
        return list(expr.elems)
    if isinstance(expr, A.TupleLit):
        return list(expr.elems)
    raise LowerError("thread args must be a literal list/tuple")


def _literal_call_kwargs(expr: A.Expr) -> list[tuple[str, A.Expr]]:
    if A.is_none_expr(expr):
        return []
    if not isinstance(expr, A.DictLit):
        raise LowerError("thread kwargs must be a literal dict")
    out: list[tuple[str, A.Expr]] = []
    for key_e, val_e in zip(expr.keys, expr.values):
        if not isinstance(key_e, A.StrLit):
            raise LowerError("thread kwargs keys must be string literals")
        out.append((key_e.value, val_e))
    return out


def _bind_thread_target_args(param_names: list, param_defaults: list, pos_args: list[A.Expr],
                             kw_items: list[tuple[str, A.Expr]]) -> list[A.Expr]:
    total = len(param_names)
    if len(pos_args) > total:
        raise LowerError("thread target received too many positional arguments")
    bound: list[A.Expr | None] = [None] * total
    for i, expr in enumerate(pos_args):
        bound[i] = expr
    for name, expr in kw_items:
        idx = -1
        for i, pname in enumerate(param_names):
            if pname == name:
                idx = i
                break
        if idx < 0:
            raise LowerError(f"thread target got unexpected keyword {name!r}")
        if bound[idx] is not None:
            raise LowerError(f"thread target got multiple values for {name!r}")
        bound[idx] = expr
    out: list[A.Expr] = []
    for i, expr in enumerate(bound):
        if expr is not None:
            out.append(expr)
            continue
        default = param_defaults[i] if i < len(param_defaults) else None
        if default is None:
            raise LowerError("thread target is missing a required argument")
        out.append(default)
    return out


def _resolve_thread_target(ctx: _FuncCtx, target_e: A.Expr, args_e: A.Expr, kwargs_e: A.Expr
                           ) -> tuple[IRValue, IRValue, list[A.Expr]]:
    zero_ptr = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", zero_ptr, [0]))
    pos_args = _literal_call_args_list(args_e)
    kw_items = _literal_call_kwargs(kwargs_e)

    if isinstance(target_e, A.Attr):
        obj_ty = A.expr_type(target_e.obj)
        if obj_ty.startswith("instance:"):
            cls_name = obj_ty.split(":", 1)[1]
            owner = _resolve_method_owner(ctx, cls_name, target_e.name)
            if owner is not None:
                sig_cls = ctx.mctx.classes_sig.get(owner)
                if sig_cls is not None and target_e.name in sig_cls.methods:
                    sig = sig_cls.methods[target_e.name]
                    bound_args = _bind_thread_target_args(
                        list(sig.param_names[1:]),
                        list(sig.param_defaults[1:]),
                        pos_args,
                        kw_items,
                    )
                    target_ptr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("global_addr", target_ptr, [f"{owner}__{target_e.name}"]))
                    target_self = _lower_expr(ctx, target_e.obj)
                    return target_ptr, target_self, bound_args

    if isinstance(target_e, A.Name) and kw_items and target_e.name in ctx.mctx.func_sigs:
        sig = ctx.mctx.func_sigs[target_e.name]
        pos_args = _bind_thread_target_args(
            list(sig.param_names),
            list(sig.param_defaults),
            pos_args,
            kw_items,
        )
        kw_items = []

    if kw_items:
        raise LowerError("thread kwargs require a statically known target signature")

    target_ptr = _lower_expr(ctx, target_e)
    return target_ptr, zero_ptr, pos_args


def _lower_thread_ctor(ctx: _FuncCtx, e: A.Call) -> IRValue:
    obj_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", obj_v, ["_abi_new_instance"]))

    target_e = e.args[0] if len(e.args) >= 1 else A.IntLit(value=0, pos=e.pos)
    name_e = e.args[1] if len(e.args) >= 2 else A.StrLit(value="", pos=e.pos)
    args_e = e.args[2] if len(e.args) >= 3 else A.Name(name="None", pos=e.pos)
    kwargs_e = e.args[3] if len(e.args) >= 4 else A.Name(name="None", pos=e.pos)
    daemon_e = e.args[4] if len(e.args) >= 5 else A.IntLit(value=0, pos=e.pos)

    target_ptr, target_self, call_args = _resolve_thread_target(ctx, target_e, args_e, kwargs_e)
    if len(call_args) > 8:
        raise LowerError("thread targets with more than 8 arguments are not supported yet")

    _emit_instance_field_set(ctx, obj_v, "target", target_ptr)
    _emit_instance_field_set(ctx, obj_v, "target_self", target_self)
    _emit_instance_field_set(ctx, obj_v, "name", _lower_expr(ctx, name_e))
    _emit_instance_field_set(ctx, obj_v, "args", _emit_list_from_exprs(ctx, call_args))
    _emit_instance_field_set(ctx, obj_v, "kwargs", _emit_dict_from_literal(ctx, kwargs_e))
    _emit_instance_field_set(ctx, obj_v, "daemon", _lower_expr(ctx, daemon_e))

    argc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", argc_v, [len(call_args)]))
    _emit_instance_field_set(ctx, obj_v, "_argc", argc_v)

    zero_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero_v, [0]))
    for i in range(8):
        arg_v = _lower_expr(ctx, call_args[i]) if i < len(call_args) else zero_v
        _emit_instance_field_set(ctx, obj_v, f"_arg{i}", arg_v)

    empty_name = ctx.mctx.intern_str("")
    empty_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
    _emit_instance_field_set(ctx, obj_v, "_handle", empty_v)
    _emit_instance_field_set(ctx, obj_v, "_alive", zero_v)
    return obj_v


def _lower_expr(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    if isinstance(e, A.IntLit):
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", v, [int(e.value)]))
        return v

    if isinstance(e, A.FloatLit):
        v = ctx.tmp(F64)
        ctx.emit(IRInstr("const", v, [float(e.value)]))
        return v

    if isinstance(e, A.Name):
        if e.name in ctx.mctx.func_names and e.name not in ctx.slot_ty:
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", v, [e.name]))
            return v
        if e.name in ctx.mctx.class_ids:
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", v, [ctx.mctx.class_ids[e.name]]))
            return v
        if e.name in BUILTIN_TYPE_IDS:
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", v, [BUILTIN_TYPE_IDS[e.name]]))
            return v
        # A local can shadow an unrelated module-level global of the same
        # name (e.g. `x = 0.0` at module scope, `for x in xs:` inside a
        # function that never declared `global x`) -- when that's the
        # case, this read must use the LOCAL slot's own type, not the
        # unrelated global's, or a stale/mismatched-type load corrupts
        # downstream register-class selection (int vs xmm). Mirrors
        # _name_ptr's own is-this-actually-global check.
        if _is_global_name(ctx, e.name):
            ty = ctx.mctx.global_types.get(e.name, ctx.slot_ty.get(e.name, I64))
        else:
            ty = ctx.slot_ty.get(e.name, ctx.mctx.global_types.get(e.name, I64))
        ptr = _name_ptr(ctx, e.name, ty)
        v = ctx.tmp(ty)
        ctx.emit(IRInstr("load", v, [ptr]))
        return v

    if isinstance(e, A.Lambda):
        name = getattr(e, "func_name", "")
        if not name:
            raise LowerError("unsupported expr Lambda (missing func_name)")
        v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", v, [name]))
        return v

    if isinstance(e, A.NamedExpr):
        # `target := value` -- evaluate value, store into target's slot
        # (same scope resolution as a plain `target = value` Assign), and
        # yield the value so the enclosing expression can use it.
        val = _lower_expr(ctx, e.value)
        ptr = _name_ptr(ctx, e.target, ctx.mctx.global_types.get(e.target, val.type))
        ctx.emit(IRInstr("store", None, [val, ptr]))
        if not _is_global_name(ctx, e.target) and A.expr_type(e.value) == "list":
            ctx.slot_el_ty[e.target] = getattr(e.value, "list_el_type", "int")
        return val

    if isinstance(e, A.UnaryOp):
        owner = getattr(e, "dunder_owner", None)
        if owner is not None:
            # `-instance`/`+instance`/`~instance` where sema resolved a
            # real `__neg__`/`__pos__`/`__invert__` method on the operand's
            # class (see sema.py's UnaryOp check, which stamps dunder_owner/
            # dunder_method the same way BinOp's dunder overload check
            # does, just above this one). Without this check, the operand
            # -- a real object/instance pointer -- silently fell through to
            # the plain-int `ineg`/`inot` path below (or, for `+`, was
            # returned completely unmodified, skipping __pos__ entirely),
            # treating the pointer as a raw integer to negate/invert --
            # corrupts it into a bogus address, confirmed via gdb crashing
            # deep in application code on the very next dereference of the
            # "negated" pointer (test case: `-a` on a `Vec` instance with a
            # real `__neg__`).
            method = e.dunder_method  # type: ignore[attr-defined]
            operand_v = _lower_expr(ctx, e.operand)
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [f"{owner}__{method}", operand_v]))
            return v
        operand_ty = A.expr_type(e.operand)
        if e.op == "+":
            return _lower_expr(ctx, e.operand)
        if e.op == "not":
            # Boolean negation always yields int 0/1, whatever the operand's
            # own type (matches ast_nodes.expr_type's UnaryOp special case).
            r = ctx.tmp(I64)
            if operand_ty == "float":
                v = _lower_expr(ctx, e.operand)
                zero = ctx.tmp(F64)
                ctx.emit(IRInstr("const", zero, [0.0]))
                ctx.emit(IRInstr("fcmp.eq", r, [v, zero]))
            else:
                v = _lower_truthy(ctx, e.operand)
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                ctx.emit(IRInstr("icmp.eq", r, [v, zero]))
            return r
        if operand_ty == "float":
            if e.op != "-":
                raise LowerError(f"unsupported float unary op {e.op!r}")
            v = _lower_expr(ctx, e.operand)
            r = ctx.tmp(F64)
            ctx.emit(IRInstr("fneg", r, [v]))
            return r
        v = _lower_expr(ctx, e.operand)
        r = ctx.tmp(I64)
        if e.op == "-":
            ctx.emit(IRInstr("ineg", r, [v]))
        elif e.op == "~":
            ctx.emit(IRInstr("inot", r, [v]))
        else:
            raise LowerError(f"unsupported unary op {e.op!r}")
        return r

    if isinstance(e, A.BoolOp):
        # Short-circuit: evaluate `left`; if and/or's shortcut condition is
        # met, that's the result (whichever value it already is, matching
        # Python's "and"/"or" returning an operand, not a forced bool);
        # otherwise evaluate and use `right`. Both arms are stored through a
        # shared slot (the existing memory-SSA pattern) rather than a phi,
        # since they may produce values of different IRTypes (int vs float)
        # only in already-ruled-out-by-sema mixed cases -- in practice both
        # arms share expr_type's promoted type, used here for the slot.
        res_ty = ir_type_for(A.expr_type(e))
        tmp_name = f"__boolop_{id(e)}"
        ptr = ctx.ensure_slot(tmp_name, res_ty)
        left_v = _lower_expr(ctx, e.left)
        ctx.emit(IRInstr("store", None, [left_v, ptr]))
        cond = _value_truthy(ctx, left_v)

        rhs_b = ctx.new_block("boolrhs")
        merge_b = ctx.new_block("boolend")
        if e.op == "and":
            # truthy(left) -> left doesn't short-circuit, evaluate right.
            ctx.emit(IRInstr("br.t", None, [cond, rhs_b.label, merge_b.label]))
        elif e.op == "or":
            # truthy(left) -> short-circuits on left, skip right entirely.
            ctx.emit(IRInstr("br.t", None, [cond, merge_b.label, rhs_b.label]))
        else:
            raise LowerError(f"unsupported boolop {e.op!r}")

        ctx.switch_to(rhs_b)
        right_v = _lower_expr(ctx, e.right)
        ctx.emit(IRInstr("store", None, [right_v, ptr]))
        ctx.emit(IRInstr("br", None, [merge_b.label]))

        ctx.switch_to(merge_b)
        v = ctx.tmp(res_ty)
        ctx.emit(IRInstr("load", v, [ptr]))
        return v

    if isinstance(e, A.IfExp):
        res_ty = ir_type_for(A.expr_type(e))
        tmp_name = f"__ifexp_{id(e)}"
        ptr = ctx.ensure_slot(tmp_name, res_ty)

        then_b = ctx.new_block("ifexpthen")
        else_b = ctx.new_block("ifexpelse")
        merge_b = ctx.new_block("ifexpend")
        cond = _lower_truthy(ctx, e.test)
        ctx.emit(IRInstr("br.t", None, [cond, then_b.label, else_b.label]))

        ctx.switch_to(then_b)
        body_v = _lower_expr(ctx, e.body)
        ctx.emit(IRInstr("store", None, [body_v, ptr]))
        ctx.emit(IRInstr("br", None, [merge_b.label]))

        ctx.switch_to(else_b)
        orelse_v = _lower_expr(ctx, e.orelse)
        ctx.emit(IRInstr("store", None, [orelse_v, ptr]))
        ctx.emit(IRInstr("br", None, [merge_b.label]))

        ctx.switch_to(merge_b)
        v = ctx.tmp(res_ty)
        ctx.emit(IRInstr("load", v, [ptr]))
        return v

    if isinstance(e, A.BinOp):
        lt, rt = A.expr_type(e.left), A.expr_type(e.right)
        owner = getattr(e, "dunder_owner", None)
        if owner is not None:
            method = e.dunder_method  # type: ignore[attr-defined]
            reflected = getattr(e, "dunder_reflected", False)
            lhs = _lower_expr(ctx, e.left)
            rhs = _lower_expr(ctx, e.right)
            args = [rhs, lhs] if reflected else [lhs, rhs]
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [f"{owner}__{method}", *args]))
            return v
        if e.op == "+" and "str" in (lt, rt):
            lhs = _lower_expr(ctx, e.left)
            rhs = _lower_expr(ctx, e.right)
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", v, ["_abi_str_concat", lhs, rhs]))
            return v
        if e.op == "%" and lt == "str":
            # `"...%s/%d/%f..." % (args)` -- sema already validated e.left
            # is a literal format string and stamped nothing extra; parse
            # it the same way sema did (A.parse_pct_format is the single
            # shared parser both sides use, so they can't drift) and
            # lower to the same lit/arg concat chain as f-strings.
            return _lower_pct_format(ctx, e)
        if e.op == "*" and "str" in (lt, rt):
            # `"str" * int` / `int * "str"` (repeat) -- was previously
            # falling through to the plain-int `imul` path below, which
            # multiplied the string's raw POINTER value by the count and
            # fed the resulting garbage address to printf as if it were a
            # real string -- corrupts printf's internal state badly enough
            # to crash inside libc itself (confirmed via gdb: SIGSEGV
            # inside msvcrt.dll's ungetwc, called from printf), not just
            # "prints garbage". `_runtime_str_repeat` (rax=str ptr,
            # rbx=count) always wants the string first regardless of
            # source order, matching codegen.py's `_gen_binop_str`.
            str_e, count_e = (e.left, e.right) if lt == "str" else (e.right, e.left)
            str_v = _lower_expr(ctx, str_e)
            count_v = _lower_expr(ctx, count_e)
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", v, ["_abi_str_repeat", str_v, count_v]))
            return v
        if e.op == "+" and "list" in (lt, rt):
            # `xs + ys` (list concat, also reached via `xs += ys`'s
            # AugAssign-as-BinOp normalization) -- previously fell through
            # entirely to the plain-int `+` path at the bottom of this
            # function, adding the two lists' raw HEADER POINTERS together
            # as if they were integers and returning the resulting bogus
            # address as the "concatenated list" -- corrupts immediately on
            # the very next `len()`/index read (confirmed via gdb). Mirrors
            # codegen.py's `_runtime_list_slice` (full-range shallow copy of
            # the left operand, so the original `xs` is never mutated) then
            # `_runtime_list_extend` (append every element of the right
            # operand onto that copy) pattern exactly.
            lhs = _lower_expr(ctx, e.left)
            min_v = ctx.tmp(I64)
            max_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", min_v, [-9223372036854775808]))
            ctx.emit(IRInstr("const", max_v, [9223372036854775807]))
            copy_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", copy_v, ["_abi_list_slice", lhs, min_v, max_v]))
            rhs = _lower_expr(ctx, e.right)
            ctx.emit(IRInstr("call", None, ["_abi_list_extend", copy_v, rhs]))
            return copy_v
        if e.op == "*" and "list" in (lt, rt):
            # `xs * n` / `n * xs` (list repetition) -- same missing-case
            # shape as the `+` concat fix just above; falls through to
            # `_abi_list_repeat` exactly like codegen.py's
            # `_runtime_list_repeat`.
            list_e, count_e = (e.left, e.right) if lt == "list" else (e.right, e.left)
            list_v = _lower_expr(ctx, list_e)
            count_v = _lower_expr(ctx, count_e)
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", v, ["_abi_list_repeat", list_v, count_v]))
            return v
        if lt in ("dict", "set") and rt in ("dict", "set") and e.op == "|":
            # `d1 | d2` (PEP 584): a fresh dict/set with left's entries then
            # right's merged on top (right wins on key conflicts) -- same
            # "new = {}; new.update(left); new.update(right)" codegen.py
            # uses for both dict union and set union (sets are dicts keyed
            # by member, so _abi_dict_update already does the right thing
            # for either).
            lhs = _lower_expr(ctx, e.left)
            rhs = _lower_expr(ctx, e.right)
            new_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", new_v, ["_abi_new_instance"]))
            ctx.emit(IRInstr("call", None, ["_abi_dict_update", new_v, lhs]))
            ctx.emit(IRInstr("call", None, ["_abi_dict_update", new_v, rhs]))
            return new_v
        if lt == "float" or rt == "float":
            if e.op not in _FBINOP and e.op not in ("%", "**"):
                raise LowerError(f"unsupported float binop {e.op!r}")
            a = _lower_expr(ctx, e.left)
            if lt != "float":
                a_f = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", a_f, [a]))
                a = a_f
            b = _lower_expr(ctx, e.right)
            if rt != "float":
                b_f = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", b_f, [b]))
                b = b_f
            if e.op in ("%", "**"):
                # No direct SSE instruction for float mod/pow -- both route
                # through the matching real libc double(double,double)
                # export (fmod/pow), same as codegen.py's
                # _emit_call_libc_double_double.
                v = ctx.tmp(F64)
                c_name = "fmod" if e.op == "%" else "pow"
                ctx.emit(IRInstr("call", v, [c_name, a, b]))
                return v
            v = ctx.tmp(F64)
            ctx.emit(IRInstr(_FBINOP[e.op], v, [a, b]))
            return v
        if e.op == "**":
            a = _lower_expr(ctx, e.left)
            b = _lower_expr(ctx, e.right)
            return _lower_int_pow(ctx, a, b, id(e))
        if e.op in ("//", "%"):
            # Plain "idiv"/"irem" truncate toward zero (raw x86 IDIV); //
            # and % need floor-toward-(-inf) semantics -- see
            # _lower_int_floordivmod's docstring.
            a = _lower_expr(ctx, e.left)
            b = _lower_expr(ctx, e.right)
            return _lower_int_floordivmod(ctx, a, b, e.op, id(e))
        if e.op not in _BINOP:
            raise LowerError(f"unsupported binop {e.op!r}")
        a = _lower_expr(ctx, e.left)
        b = _lower_expr(ctx, e.right)
        v = ctx.tmp(I64)
        ctx.emit(IRInstr(_BINOP[e.op], v, [a, b]))
        return v

    if isinstance(e, A.Compare):
        if len(e.ops) == 1 and e.ops[0] in ("in", "not in"):
            contains_owner = getattr(e, "dunder_contains_owner", None)
            if contains_owner is not None:
                # `x in obj` / `x not in obj` where sema resolved a real
                # `__contains__` on obj's class -- _lower_membership only
                # ever handles dict/set/list/tuple haystacks and raises a
                # clean LowerError otherwise, so a custom-`__contains__`
                # class (e.g. `367_custom_contains.py`'s Bag) previously
                # failed to build at all rather than silently misbehaving.
                needle_v = _lower_expr(ctx, e.operands[0])
                hay_v = _lower_expr(ctx, e.operands[1])
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, [f"{contains_owner}____contains__", hay_v, needle_v]))
                if getattr(e, "dunder_contains_negate", False):
                    zero = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", zero, [0]))
                    inv = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.eq", inv, [v, zero]))
                    return inv
                return v
            return _lower_membership(
                ctx, e.operands[0], e.operands[1], e.ops[0] == "not in"
            )
        owner = getattr(e, "dunder_owner", None)
        if owner is not None and len(e.ops) == 1:
            # `a == b` / `a < b` / etc. where sema resolved a real
            # `__eq__`/`__lt__`/etc. on one side's class (mirrors A.BinOp's
            # dunder_owner check above -- this one was missing entirely
            # until this fix, so every instance comparison silently fell
            # through to the chained-comparison path below, which compares
            # the two operands' raw pointer values (object identity) --
            # not a crash, but silently wrong whenever a class defines a
            # real __eq__/__lt__/etc. Confirmed via a minimal repro: a
            # Point class with `__eq__` comparing x/y fields printed False
            # for two field-equal-but-distinct Point instances.
            method = e.dunder_method  # type: ignore[attr-defined]
            reflected = getattr(e, "dunder_reflected", False)
            negate = getattr(e, "dunder_negate", False)
            lhs = _lower_expr(ctx, e.operands[0])
            rhs = _lower_expr(ctx, e.operands[1])
            args = [rhs, lhs] if reflected else [lhs, rhs]
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("call", v, [f"{owner}__{method}", *args]))
            if negate:
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                inv = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", inv, [v, zero]))
                return inv
            return v
        if len(e.ops) == 1:
            lt0 = A.expr_type(e.operands[0])
            rt0 = A.expr_type(e.operands[1])
            if (
                (lt0 in ("str", "any") and rt0 in ("str", "any") and "str" in (lt0, rt0))
                or getattr(e, "_map_val_str_cmp", False)
            ):
                lhs = _lower_expr(ctx, e.operands[0])
                rhs = _lower_expr(ctx, e.operands[1])
                op = e.ops[0]
                if op in ("==", "!="):
                    result = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", result, ["_abi_str_eq", lhs, rhs]))
                    if op == "!=":
                        zero = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", zero, [0]))
                        inv = ctx.tmp(I64)
                        ctx.emit(IRInstr("icmp.eq", inv, [result, zero]))
                        return inv
                    return result
                if op in ("<", "<=", ">", ">="):
                    cmp_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", cmp_v, ["_abi_str_cmp", lhs, rhs]))
                    zero = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", zero, [0]))
                    result = ctx.tmp(I64)
                    pred = {
                        "<": "icmp.lt",
                        "<=": "icmp.le",
                        ">": "icmp.gt",
                        ">=": "icmp.ge",
                    }[op]
                    ctx.emit(IRInstr(pred, result, [cmp_v, zero]))
                    return result
        # Chained comparison a < b < c -> (a < b) and (b < c). Each pair
        # picks int or float compare independently based on that pair's own
        # operand types (e.g. `1 < x < 2.0` compares the first pair as int,
        # the second as float).
        result: IRValue | None = None
        operands = [_lower_expr(ctx, e.operands[0])]
        operand_types = [A.expr_type(e.operands[0])]
        for i, op in enumerate(e.ops):
            rhs_ty = A.expr_type(e.operands[i + 1])
            rhs = _lower_expr(ctx, e.operands[i + 1])
            operands.append(rhs)
            operand_types.append(rhs_ty)
            step = ctx.tmp(I64)
            if operand_types[i] == "float" or rhs_ty == "float":
                if op not in _FCMPOP:
                    raise LowerError(f"unsupported float compare op {op!r}")
                ctx.emit(IRInstr(_FCMPOP[op], step, [operands[i], rhs]))
            else:
                if op not in _CMPOP:
                    raise LowerError(f"unsupported compare op {op!r}")
                ctx.emit(IRInstr(_CMPOP[op], step, [operands[i], rhs]))
            if result is None:
                result = step
            else:
                anded = ctx.tmp(I64)
                ctx.emit(IRInstr("iand", anded, [result, step]))
                result = anded
        assert result is not None
        return result

    if isinstance(e, A.StrLit):
        name = ctx.mctx.intern_str(e.value)
        v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", v, [name]))
        return v

    if isinstance(e, A.FString):
        return _lower_fstring(ctx, e)

    if isinstance(e, A.Call) and e.func == "len" and len(e.args) == 1 and A.expr_type(e.args[0]) == "str":
        obj_v = _lower_expr(ctx, e.args[0])
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", v, ["strlen", obj_v]))
        return v

    if isinstance(e, A.Call) and e.func == "len" and len(e.args) == 1 and A.expr_type(e.args[0]) in ("list", "tuple"):
        list_v = _lower_expr(ctx, e.args[0])
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [list_v, _LIST_LEN_OFF]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", v, [len_addr]))
        return v

    if isinstance(e, A.Call) and e.func == "len" and len(e.args) == 1 and A.expr_type(e.args[0]) in ("dict", "set", "any", "int"):
        obj_v = _lower_expr(ctx, e.args[0])
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", v, [len_addr]))
        return v

    if isinstance(e, A.Call) and e.func == "ord" and len(e.args) == 1:
        if A.expr_type(e.args[0]) != "str":
            raise LowerError("unsupported expr Call (ord non-str)")
        str_v = _lower_expr(ctx, e.args[0])
        ch = ctx.tmp(U8)
        ctx.emit(IRInstr("load", ch, [str_v]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("zext", v, [ch]))
        return v

    if isinstance(e, A.Call) and e.func == "callable" and len(e.args) == 1:
        arg = e.args[0]
        arg_ty = A.expr_type(arg)
        if isinstance(arg, A.Name):
            if arg.name in ctx.mctx.func_names or arg.name in ctx.mctx.class_names or arg.name in ctx.mctx.ffi_funcs:
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", v, [1]))
                return v
        if isinstance(arg, A.Attr) and isinstance(arg.obj, A.Name) and arg.obj.name in ctx.mctx.imported_modules:
            bindings = ctx.mctx.imported_modules[arg.obj.name]
            bound = bindings.get(arg.name)
            if bound is not None and hasattr(bound, "c_name"):
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", v, [1]))
                return v
        if arg_ty.startswith("instance:"):
            cls_name = arg_ty.split(":", 1)[1]
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", v, [1 if _resolve_method_owner(ctx, cls_name, "__call__") is not None else 0]))
            return v
        if arg_ty in ("any",):
            val = _lower_expr(ctx, arg)
            zero = ctx.tmp(PTR if val.type == PTR else I64)
            ctx.emit(IRInstr("const", zero, [0]))
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.ne", v, [val, zero]))
            return v
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", v, [0]))
        return v

    if isinstance(e, A.Call) and e.func == "print":
        # print(x) -> printf("%s...%s<end>", x, ...); every argument routes
        # through _lower_expr_as_str (the same repr machinery f-strings
        # use) so list/dict/tuple/set/instance/None/float args print their
        # real CPython-style text instead of a raw %lld-formatted pointer
        # value or C's bare (non-".0") %g float text. `sep`/`end` keyword
        # args (default " "/"\n") are baked directly into the literal
        # format string at compile time -- mirrors codegen.py's _gen_print,
        # which only ever supports a literal `A.StrLit` sep/end (matching
        # every real call site in this codebase; a non-literal sep/end
        # isn't attempted by either backend). This was previously entirely
        # unimplemented on this backend: sep/end were silently ignored,
        # always falling back to the CPython default " "/"\n" regardless
        # of what the call site actually passed.
        sep_str = " "
        end_str = "\n"
        for kn, kv in e.kwargs:
            if kn == "sep" and isinstance(kv, A.StrLit):
                sep_str = kv.value
            elif kn == "end" and isinstance(kv, A.StrLit):
                end_str = kv.value
        # sep/end are baked directly into the printf format string (unlike
        # codegen.py's approach of emitting them as separate literal-string
        # writes) -- a literal `%` in either would otherwise be misread as
        # a conversion specifier by printf itself. Escape defensively.
        sep_str = sep_str.replace("%", "%%")
        end_str = end_str.replace("%", "%%")
        if not e.args:
            fmt_ptr = ctx.tmp(PTR)
            if end_str:
                fmt_name = ctx.mctx.intern_str(end_str)
                ctx.emit(IRInstr("global_addr", fmt_ptr, [fmt_name]))
                ctx.emit(IRInstr("call", None, ["printf", fmt_ptr]))
        else:
            fmt_parts = ["%s"] * len(e.args)
            call_args = [_lower_expr_as_str(ctx, arg) for arg in e.args]
            fmt_name = ctx.mctx.intern_str(sep_str.join(fmt_parts) + end_str)
            fmt_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", fmt_ptr, [fmt_name]))
            ctx.emit(IRInstr("call", None, ["printf", fmt_ptr, *call_args]))
        return ctx.shared_zero

    if isinstance(e, A.Call) and e.func == "getattr" and len(e.args) in (2, 3):
        res_ty = ir_type_for(A.expr_type(e))
        obj_v = _lower_expr(ctx, e.args[0])
        name_v = _lower_expr(ctx, e.args[1])
        if len(e.args) == 3:
            default_v = _lower_expr(ctx, e.args[2])
        else:
            default_v = ctx.shared_zero
        res_ptr = ctx.ensure_slot(f"__getattr_res_{id(e)}", res_ty)
        none_b = ctx.new_block("getattrnone")
        live_b = ctx.new_block("getattrlive")
        end_b = ctx.new_block("getattrend")
        zero = ctx.tmp(PTR if obj_v.type == PTR else I64)
        ctx.emit(IRInstr("const", zero, [0]))
        is_none = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", is_none, [obj_v, zero]))
        ctx.emit(IRInstr("br.t", None, [is_none, none_b.label, live_b.label]))

        ctx.switch_to(none_b)
        ctx.emit(IRInstr("store", None, [default_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(live_b)
        got_v = ctx.tmp(res_ty)
        ctx.emit(IRInstr("call", got_v, ["_abi_dict_get_default", obj_v, name_v, default_v]))
        ctx.emit(IRInstr("store", None, [got_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        out = ctx.tmp(res_ty)
        ctx.emit(IRInstr("load", out, [res_ptr]))
        return out

    if isinstance(e, A.Call) and e.func == "setattr" and len(e.args) == 3:
        obj_v = _lower_expr(ctx, e.args[0])
        name_v = _lower_expr(ctx, e.args[1])
        val_v = _lower_expr(ctx, e.args[2])
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, name_v, val_v]))
        return ctx.shared_zero

    if isinstance(e, A.Call) and e.func == "isinstance" and len(e.args) == 2:
        return _lower_isinstance(ctx, e)

    if isinstance(e, A.Call) and e.func in ("bytes", "bytearray"):
        if not e.args:
            one = ctx.tmp(I64)
            ctx.emit(IRInstr("const", one, [1]))
            out_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out_v, ["_abi_new_list", one]))
            return out_v
        arg_ty = A.expr_type(e.args[0])
        if arg_ty in ("list", "tuple"):
            return _lower_expr(ctx, e.args[0])
        if arg_ty == "int":
            return _lower_zero_byte_list(ctx, e.args[0])
        if arg_ty == "str":
            return _lower_str_to_byte_list(ctx, e.args[0])
        raise LowerError(f"unsupported expr Call ({e.func} {arg_ty})")

    if isinstance(e, A.ListLit):
        n = len(e.elems)
        cap_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", cap_v, [max(n, 1)]))
        list_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", list_v, ["_abi_new_list", cap_v]))
        for el in e.elems:
            if isinstance(el, A.Starred):
                other_v = _lower_expr(ctx, el.value)
                ctx.emit(IRInstr("call", None, ["_abi_list_extend", list_v, other_v]))
                continue
            val = _lower_expr(ctx, el)
            if A.expr_type(el) == "float":
                # _abi_list_append's cell is a plain 8-byte int slot (same
                # constraint as _abi_dict_set -- see A.AttrAssign's
                # matching comment); store the float's raw bits, read back
                # via bitcast_i2f wherever an element is loaded as "float".
                iv = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
                val = iv
            ctx.emit(IRInstr("call", None, ["_abi_list_append", list_v, val]))
        return list_v

    if isinstance(e, A.Comprehension):
        return _lower_comprehension(ctx, e)
    if isinstance(e, A.DictComprehension):
        return _lower_dict_comprehension(ctx, e)

    if isinstance(e, A.TupleLit):
        # Reuses the list layout exactly (per ast_nodes.py's TupleLit
        # docstring) but elements may be heterogeneous int/float, and the
        # size is fixed at construction -- so build it via direct buffer
        # stores at each known compile-time offset rather than
        # _abi_list_append (which only handles single-typed int/ptr
        # elements for ListLit).
        n = len(e.elems)
        cap_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", cap_v, [max(n, 1)]))
        tup_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", tup_v, ["_abi_new_list", cap_v]))
        if n:
            buf_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", buf_addr, [tup_v, _LIST_BUF_OFF]))
            buf_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", buf_v, [buf_addr]))
            for i, el in enumerate(e.elems):
                val = _lower_expr(ctx, el)
                slot_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", slot_addr, [buf_v, i * 8]))
                ctx.emit(IRInstr("store", None, [val, slot_addr]))
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [tup_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", len_v, [n]))
        ctx.emit(IRInstr("store", None, [len_v, len_addr]))
        return tup_v

    if isinstance(e, A.DictLit):
        dict_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", dict_v, ["_abi_new_instance"]))
        for k, v in zip(e.keys, e.values):
            if isinstance(k, A.Name) and k.name == "**":
                # `**other` (PEP 448): sema represents a dict-literal
                # spread as a sentinel Name("**") key (not a None key --
                # that check never matched, silently falling through to
                # evaluating Name("**") as an ordinary key expression and
                # inserting garbage). Merge other's entries in, in source
                # order, so later entries win on key conflicts -- same
                # semantics as dict.update().
                other_v = _lower_expr(ctx, v)
                ctx.emit(IRInstr("call", None, ["_abi_dict_update", dict_v, other_v]))
                continue
            key_ptr = _lower_dict_key(ctx, k)
            val = _lower_expr(ctx, v)
            if A.expr_type(v) == "float":
                # Same int-only-cell constraint as A.AttrAssign/A.ListLit.
                iv = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
                val = iv
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", dict_v, key_ptr, val]))
        return dict_v

    if isinstance(e, A.SetLit):
        set_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", set_v, ["_abi_new_instance"]))
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        for el in e.elems:
            key_ptr = _lower_dict_key(ctx, el)
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", set_v, key_ptr, one_v]))
        return set_v

    if isinstance(e, A.Subscript):
        if isinstance(e.index, A.Slice):
            obj_ty = A.expr_type(e.obj)
            if obj_ty in ("list", "tuple") and e.index.step is None:
                obj_v = _lower_expr(ctx, e.obj)
                if e.index.start is None:
                    start_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", start_v, [-9223372036854775808]))
                else:
                    start_v = _lower_expr(ctx, e.index.start)
                if e.index.stop is None:
                    stop_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", stop_v, [9223372036854775807]))
                else:
                    stop_v = _lower_expr(ctx, e.index.stop)
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_list_slice", obj_v, start_v, stop_v]))
                return v
            if obj_ty == "str" and e.index.step is None:
                obj_v = _lower_expr(ctx, e.obj)
                if e.index.start is None:
                    start_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", start_v, [0]))
                else:
                    start_v = _lower_expr(ctx, e.index.start)
                if e.index.stop is None:
                    stop_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", stop_v, ["strlen", obj_v]))
                else:
                    stop_v = _lower_expr(ctx, e.index.stop)
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_slice", obj_v, start_v, stop_v]))
                return v
            raise LowerError("unsupported expr Subscript (slice)")
        obj_ty = A.expr_type(e.obj)
        if obj_ty == "str":
            if A.expr_type(e.index) != "int":
                raise LowerError("unsupported expr Subscript (non-int str index)")
            obj_v = _lower_expr(ctx, e.obj)
            idx_v = _lower_expr(ctx, e.index)
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", v, ["_abi_str_char_at", obj_v, idx_v]))
            return v
        if obj_ty == "dict":
            if A.expr_type(e) == "float":
                raise LowerError("unsupported expr Subscript (float dict value)")
            obj_v = _lower_expr(ctx, e.obj)
            key_v = _lower_dict_key(ctx, e.index)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_v, key_v, zero]))
            return v
        getitem_cls = getattr(e, "_getitem_class", "")
        if getitem_cls or obj_ty.startswith("instance:"):
            cls_name = getitem_cls or obj_ty.split(":", 1)[1]
            obj_v = _lower_expr(ctx, e.obj)
            idx_v = _lower_expr(ctx, e.index)
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [f"{cls_name}____getitem__", obj_v, idx_v]))
            return v
        if obj_ty not in ("list", "tuple"):
            raise LowerError(f"unsupported expr Subscript ({obj_ty})")
        result_ty = A.expr_type(e)
        obj_v = _lower_expr(ctx, e.obj)
        idx_v = _lower_expr(ctx, e.index)
        addr = _list_elem_addr(ctx, obj_v, idx_v)
        v = ctx.tmp(F64 if result_ty == "float" else I64)
        ctx.emit(IRInstr("load", v, [addr]))
        return v

    if isinstance(e, A.MethodCall):
        obj_ty = A.expr_type(e.obj)
        if obj_ty == "int" and e.method == "to_bytes":
            return _lower_int_to_bytes(ctx, e)
        if isinstance(e.obj, A.Name) and e.obj.name in ctx.mctx.imported_modules:
            # `os.getcwd()`/`os.cpu_count()`: inline codegen helpers, not
            # real BINDINGS entries (no single C symbol covers Python's
            # `getcwd()` semantics -- it needs a scratch buffer plus a
            # dup, matching codegen.py's `_emit_os_getcwd`/sema.py's
            # matching special case). This backend previously had NO
            # handling at all for either -- both fell through to the
            # generic "unknown method on opaque receiver" stub further
            # down, which evaluates the receiver/args for side effects
            # and returns a plain 0 -- but sema had already typed the
            # call's result as `str`/`list`, so the caller's later use
            # of that "0" as a string/list pointer crashed immediately
            # (confirmed via gdb on `os.getcwd()`; codegen.py handles the
            # identical call correctly, confirming this was backend-
            # specific, not a stdlib/merge issue). Uses `malloc` for the
            # scratch buffer rather than porting codegen.py's static
            # `_cwd_buf` BSS reservation -- this backend's IRGlobal has
            # no raw-byte-buffer reservation mechanism yet, and a runtime
            # malloc is just as correct for a call this infrequent.
            if e.obj.name == "os" and e.method == "getcwd" and not e.args:
                size_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", size_v, [4096]))
                buf_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", buf_v, ["malloc", size_v]))
                got_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", got_v, ["_getcwd", buf_v, size_v]))
                zero_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("const", zero_v, [0]))
                ok_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.ne", ok_v, [got_v, zero_v]))
                ok_b = ctx.new_block("getcwdok")
                fail_b = ctx.new_block("getcwdfail")
                end_b = ctx.new_block("getcwdend")
                res_ptr = ctx.ensure_slot(f"__getcwd_res_{id(e)}", PTR)
                ctx.emit(IRInstr("br.t", None, [ok_v, ok_b.label, fail_b.label]))

                ctx.switch_to(ok_b)
                dup_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", dup_v, ["_abi_str_concat_dup", buf_v]))
                ctx.emit(IRInstr("store", None, [dup_v, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

                ctx.switch_to(fail_b)
                empty_name = ctx.mctx.intern_str("")
                empty_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
                ctx.emit(IRInstr("store", None, [empty_v, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

                ctx.switch_to(end_b)
                out = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", out, [res_ptr]))
                return out
            if e.obj.name == "os" and e.method == "cpu_count" and not e.args:
                # asmpython has no nullability tracking, so (matching
                # sema.py's/codegen.py's own simplification) this is
                # always a plain positive-int constant, never the real
                # `os.cpu_count() -> int | None`.
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", v, [1]))
                return v
            bindings = ctx.mctx.imported_modules[e.obj.name]
            fn = bindings.get(e.method)
            if fn is not None and hasattr(fn, "c_name"):
                c_name = getattr(fn, "c_name_windows", None) or fn.c_name
                arg_types = getattr(fn, "arg_types", None) or ()
                args = []
                for i, a in enumerate(e.args):
                    av = _lower_expr(ctx, a)
                    # `math.sqrt(16)` etc: the binding declares a `float`
                    # parameter (the real C symbol, e.g. libm's `sqrt`,
                    # takes a double in XMM0), but an int-LITERAL argument
                    # lowers to a plain I64 value with no promotion --
                    # placed in a GP register by the call's own ABI
                    # marshaling (keyed off the VALUE's own IR type, not
                    # the callee's declared signature), so the C function
                    # read garbage from XMM0 instead of the real argument.
                    # Confirmed via 16_import_math.py: math.sqrt(16)
                    # printed 0 instead of 4. Promote here, mirroring the
                    # same int->float promotion every other numeric-binop
                    # call site in this file already does.
                    if i < len(arg_types) and arg_types[i] == "float" and av.type is not F64:
                        fv = ctx.tmp(F64)
                        ctx.emit(IRInstr("sitofp", fv, [av]))
                        av = fv
                    args.append(av)
                if c_name in ("fmax", "fmin") and len(args) == 2:
                    # Neither fmax nor fmin (nor an MS-spelled _fmax/_fmin)
                    # is a real classic-msvcrt.dll export -- SSE2's MAXSD/
                    # MINSD compute the same IEEE-754 result directly, so
                    # route through the _abi_fmax_f64/_abi_fmin_f64 shims
                    # instead of an unresolvable DLL import.
                    shim = "_abi_fmax_f64" if c_name == "fmax" else "_abi_fmin_f64"
                    v = ctx.tmp(F64)
                    ctx.emit(IRInstr("call", v, [shim, *args]))
                    return v
                if c_name == "exp2" and len(args) == 1:
                    # exp2 isn't a real msvcrt.dll export either, but
                    # exp2(x) == pow(2.0, x) exactly, and pow is.
                    two = ctx.tmp(F64)
                    ctx.emit(IRInstr("const", two, [2.0]))
                    v = ctx.tmp(F64)
                    ctx.emit(IRInstr("call", v, ["pow", two, args[0]]))
                    return v
                ret_conv = getattr(fn, "ret_conv", None)
                if ret_conv == "f2i":
                    if c_name == "trunc" and len(args) == 1:
                        # trunc(x) IS "truncate toward zero", exactly what
                        # cvttsd2si already computes -- no libm call
                        # needed at all, which conveniently sidesteps
                        # trunc not being a real msvcrt.dll export (unlike
                        # floor/ceil) that this backend's own linker (no
                        # gcc/mingw static-shim aliasing available) could
                        # otherwise resolve.
                        v = ctx.tmp(I64)
                        ctx.emit(IRInstr("fptosi", v, [args[0]]))
                        return v
                    # The C symbol (e.g. libm's floor/ceil) actually
                    # returns a double in xmm0, but asmpython's ret_type
                    # narrows it to int (matching CPython's math.floor/
                    # ceil/trunc) -- call with an f64 result then truncate
                    # toward zero, mirroring codegen.py's cvttsd2si path
                    # for the same ret_conv flag.
                    fv = ctx.tmp(F64)
                    ctx.emit(IRInstr("call", fv, [c_name, *args]))
                    v = ctx.tmp(I64)
                    ctx.emit(IRInstr("fptosi", v, [fv]))
                    return v
                ret_ty = getattr(fn, "ret_type", "int") or "int"
                v = ctx.tmp(ir_type_for(ret_ty))
                ctx.emit(IRInstr("call", v, [c_name, *args]))
                return v
        if obj_ty == "list":
            if e.method == "append" and len(e.args) == 1:
                if A.expr_type(e.args[0]) == "float":
                    raise LowerError("unsupported expr MethodCall (list.append float element)")
                obj_v = _lower_expr(ctx, e.obj)
                val = _lower_expr(ctx, e.args[0])
                ctx.emit(IRInstr("call", None, ["_abi_list_append", obj_v, val]))
                return ctx.shared_zero  # list.append() returns None
            if e.method == "insert" and len(e.args) == 2:
                if A.expr_type(e.args[1]) == "float":
                    raise LowerError("unsupported expr MethodCall (list.insert float element)")
                obj_v = _lower_expr(ctx, e.obj)
                idx_v = _lower_expr(ctx, e.args[0])
                val_v = _lower_expr(ctx, e.args[1])
                ctx.emit(IRInstr("call", None, ["_abi_list_insert", obj_v, idx_v, val_v]))
                return ctx.shared_zero
            if e.method == "pop" and not e.args:
                if A.expr_type(e) == "float":
                    raise LowerError("unsupported expr MethodCall (list.pop float element)")
                obj_v = _lower_expr(ctx, e.obj)
                v = ctx.tmp(ir_type_for(A.expr_type(e)))
                ctx.emit(IRInstr("call", v, ["_abi_list_pop", obj_v]))
                return v
            if (
                e.method == "pop"
                and len(e.args) == 1
                and isinstance(e.args[0], A.IntLit)
                and e.args[0].value == 0
            ):
                return _lower_list_pop_front(ctx, e)
            if e.method == "index":
                return _lower_list_index(ctx, e)
            if e.method == "remove":
                return _lower_list_remove(ctx, e)
            if e.method == "reverse" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                ctx.emit(IRInstr("call", None, ["_abi_list_reverse", obj_v]))
                return ctx.shared_zero
            if e.method == "extend" and len(e.args) == 1 and A.expr_type(e.args[0]) == "list":
                obj_v = _lower_expr(ctx, e.obj)
                other_v = _lower_expr(ctx, e.args[0])
                ctx.emit(IRInstr("call", None, ["_abi_list_extend", obj_v, other_v]))
                return ctx.shared_zero
            if e.method == "clear" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                ctx.emit(IRInstr("store", None, [zero, len_addr]))
                return ctx.shared_zero
            if e.method == "copy" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                start_v = ctx.tmp(I64)
                stop_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", start_v, [-9223372036854775808]))
                ctx.emit(IRInstr("const", stop_v, [9223372036854775807]))
                out = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", out, ["_abi_list_slice", obj_v, start_v, stop_v]))
                return out
            if e.method == "count" and len(e.args) == 1:
                el_t = (
                    getattr(e.obj, "list_el_type", "int")
                    if isinstance(e.obj, A.Name)
                    else "int"
                ) or "int"
                val_v = _lower_expr(ctx, e.args[0])
                obj_v = _lower_expr(ctx, e.obj)
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", len_v, [len_addr]))
                idx_ptr = ctx.ensure_slot(f"__lcount_idx_{id(e)}", I64)
                cnt_ptr = ctx.ensure_slot(f"__lcount_cnt_{id(e)}", I64)
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
                zero2 = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero2, [0]))
                ctx.emit(IRInstr("store", None, [zero2, cnt_ptr]))

                head_b = ctx.new_block("lcounthead")
                body_b = ctx.new_block("lcountbody")
                match_b = ctx.new_block("lcountmatch")
                cont_b = ctx.new_block("lcountcont")
                end_b = ctx.new_block("lcountend")

                ctx.emit(IRInstr("br", None, [head_b.label]))
                ctx.switch_to(head_b)
                idx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
                cond = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", cond, [idx_v, len_v]))
                ctx.emit(IRInstr("br.t", None, [cond, body_b.label, end_b.label]))

                ctx.switch_to(body_b)
                elem_addr = _list_elem_addr(ctx, obj_v, idx_v)
                elem_v = ctx.tmp(PTR if el_t == "str" else I64)
                ctx.emit(IRInstr("load", elem_v, [elem_addr]))
                if el_t == "str":
                    is_eq = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", is_eq, ["_abi_str_eq", elem_v, val_v]))
                else:
                    is_eq = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.eq", is_eq, [elem_v, val_v]))
                ctx.emit(IRInstr("br.t", None, [is_eq, match_b.label, cont_b.label]))

                ctx.switch_to(match_b)
                cur_cnt = ctx.tmp(I64)
                ctx.emit(IRInstr("load", cur_cnt, [cnt_ptr]))
                one_c = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one_c, [1]))
                next_cnt = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", next_cnt, [cur_cnt, one_c]))
                ctx.emit(IRInstr("store", None, [next_cnt, cnt_ptr]))
                ctx.emit(IRInstr("br", None, [cont_b.label]))

                ctx.switch_to(cont_b)
                cur_idx = ctx.tmp(I64)
                ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
                one_i = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one_i, [1]))
                next_idx = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one_i]))
                ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
                ctx.emit(IRInstr("br", None, [head_b.label]))

                ctx.switch_to(end_b)
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("load", out, [cnt_ptr]))
                return out
            if e.method == "sort":
                # In-place sort (unlike sorted(), no clone first) -- reuses
                # sorted()'s key/reverse machinery via _lower_sort_inplace,
                # sema's shared _check_sort_kwargs stamps the same
                # sort_key/sort_key_ret/sort_reverse attrs onto this
                # MethodCall node either way.
                obj_v = _lower_expr(ctx, e.obj)
                el_kind = (
                    getattr(e.obj, "list_el_type", "int")
                    if isinstance(e.obj, A.Name)
                    else "int"
                ) or "int"
                _lower_sort_inplace(ctx, e, obj_v, el_kind)
                return ctx.shared_zero  # list.sort() returns None
            raise LowerError(f"unsupported expr MethodCall (list.{e.method})")
        if obj_ty == "dict":
            if e.method == "get" and len(e.args) in (1, 2):
                obj_v = _lower_expr(ctx, e.obj)
                key_v = _lower_dict_key(ctx, e.args[0])
                res_is_float = A.expr_type(e) == "float"
                if len(e.args) == 2:
                    default_v = _lower_expr(ctx, e.args[1])
                    if res_is_float and A.expr_type(e.args[1]) == "float":
                        dv = ctx.tmp(I64)
                        ctx.emit(IRInstr("bitcast_f2i", dv, [default_v]))
                        default_v = dv
                else:
                    default_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", default_v, [0]))
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_v, key_v, default_v]))
                if res_is_float:
                    # Same int-only-cell constraint as every other dict/
                    # attribute float site -- read the bits back as a real
                    # double (see A.Attr's matching bitcast_i2f comment).
                    fv = ctx.tmp(F64)
                    ctx.emit(IRInstr("bitcast_i2f", fv, [v]))
                    return fv
                return v
            if e.method == "pop" and len(e.args) in (1, 2):
                # d.pop(key[, default]): with a default, check containment
                # first and return the default without raising when the
                # key is absent; without one, _abi_dict_pop's own
                # _runtime_dict_pop already raises KeyError on a miss.
                key_v = _lower_dict_key(ctx, e.args[0])
                res_ty = ir_type_for(A.expr_type(e))
                if len(e.args) == 2:
                    obj_v = _lower_expr(ctx, e.obj)
                    has_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", obj_v, key_v]))
                    found_b = ctx.new_block("dictpopfound")
                    missing_b = ctx.new_block("dictpopmissing")
                    end_b = ctx.new_block("dictpopend")
                    res_ptr = ctx.ensure_slot(f"__dictpop_res_{id(e)}", res_ty)
                    ctx.emit(IRInstr("br.t", None, [has_v, found_b.label, missing_b.label]))

                    ctx.switch_to(missing_b)
                    default_v = _lower_expr(ctx, e.args[1])
                    ctx.emit(IRInstr("store", None, [default_v, res_ptr]))
                    ctx.emit(IRInstr("br", None, [end_b.label]))

                    ctx.switch_to(found_b)
                    obj_v2 = _lower_expr(ctx, e.obj)
                    popped_v = ctx.tmp(res_ty)
                    ctx.emit(IRInstr("call", popped_v, ["_abi_dict_pop", obj_v2, key_v]))
                    ctx.emit(IRInstr("store", None, [popped_v, res_ptr]))
                    ctx.emit(IRInstr("br", None, [end_b.label]))

                    ctx.switch_to(end_b)
                    out = ctx.tmp(res_ty)
                    ctx.emit(IRInstr("load", out, [res_ptr]))
                    return out
                obj_v = _lower_expr(ctx, e.obj)
                v = ctx.tmp(res_ty)
                ctx.emit(IRInstr("call", v, ["_abi_dict_pop", obj_v, key_v]))
                return v
            if e.method == "update" and len(e.args) == 1:
                obj_v = _lower_expr(ctx, e.obj)
                src_v = _lower_expr(ctx, e.args[0])
                ctx.emit(IRInstr("call", None, ["_abi_dict_update", obj_v, src_v]))
                return ctx.shared_zero
            if e.method == "items" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                keys_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", obj_v]))
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [keys_v, _LIST_LEN_OFF]))
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", len_v, [len_addr]))
                out_v = _new_list_from_len(ctx, len_v)
                out_ptr = ctx.ensure_slot(f"__dictitems_out_{id(e)}", PTR)
                keys_ptr = ctx.ensure_slot(f"__dictitems_keys_{id(e)}", PTR)
                idx_ptr = ctx.ensure_slot(f"__dictitems_idx_{id(e)}", I64)
                ctx.emit(IRInstr("store", None, [out_v, out_ptr]))
                ctx.emit(IRInstr("store", None, [keys_v, keys_ptr]))
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
                head_b = ctx.new_block("dictitemshead")
                body_b = ctx.new_block("dictitemsbody")
                cont_b = ctx.new_block("dictitemscont")
                end_b = ctx.new_block("dictitemsend")
                ctx.emit(IRInstr("br", None, [head_b.label]))
                ctx.switch_to(head_b)
                idx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
                cur_keys = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", cur_keys, [keys_ptr]))
                cur_len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", cur_len_addr, [cur_keys, _LIST_LEN_OFF]))
                cur_len = ctx.tmp(I64)
                ctx.emit(IRInstr("load", cur_len, [cur_len_addr]))
                keep_going = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, cur_len]))
                ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))
                ctx.switch_to(body_b)
                body_keys = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", body_keys, [keys_ptr]))
                body_idx = ctx.tmp(I64)
                ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
                key_addr = _list_elem_addr(ctx, body_keys, body_idx)
                key_item = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", key_item, [key_addr]))
                pair_cap = ctx.tmp(I64)
                ctx.emit(IRInstr("const", pair_cap, [2]))
                pair_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", pair_v, ["_abi_new_list", pair_cap]))
                ctx.emit(IRInstr("call", None, ["_abi_list_append", pair_v, key_item]))
                val_ty = e.tuple_elem_types[1] if len(e.tuple_elem_types) >= 2 else "any"
                default_v = ctx.tmp(ir_type_for(val_ty))
                ctx.emit(IRInstr("const", default_v, [0]))
                val_v = ctx.tmp(ir_type_for(val_ty))
                ctx.emit(IRInstr("call", val_v, ["_abi_dict_get_default", obj_v, key_item, default_v]))
                ctx.emit(IRInstr("call", None, ["_abi_list_append", pair_v, val_v]))
                cur_out = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", cur_out, [out_ptr]))
                ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out, pair_v]))
                ctx.emit(IRInstr("br", None, [cont_b.label]))
                ctx.switch_to(cont_b)
                cur_idx = ctx.tmp(I64)
                ctx.emit(IRInstr("load", cur_idx, [idx_ptr]))
                one = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one, [1]))
                next_idx = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", next_idx, [cur_idx, one]))
                ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
                ctx.emit(IRInstr("br", None, [head_b.label]))
                ctx.switch_to(end_b)
                final_out = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", final_out, [out_ptr]))
                return final_out
            raise LowerError(f"unsupported expr MethodCall (dict.{e.method})")
        if obj_ty == "set":
            # Sets are dicts keyed by their members (dummy value 1, str
            # keys only) -- every mutator maps onto the dict runtime, same
            # design as codegen.py's set handling.
            def _set_member_key(arg: A.Expr) -> IRValue:
                key_v = _lower_expr(ctx, arg)
                if A.expr_type(arg) == "int":
                    # A static buffer from _abi_int_to_str isn't safe to
                    # store as a long-lived dict key (it's overwritten by
                    # the next int->str conversion) -- duplicate it first.
                    s_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", s_v, ["_abi_int_to_str", key_v]))
                    dup_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", dup_v, ["_abi_str_concat_dup", s_v]))
                    return dup_v
                return key_v
            if e.method == "add" and len(e.args) == 1:
                key_v = _set_member_key(e.args[0])
                obj_v = _lower_expr(ctx, e.obj)
                one_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one_v, [1]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_v, one_v]))
                return ctx.shared_zero
            if e.method == "clear" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                ctx.emit(IRInstr("call", None, ["_abi_dict_clear", obj_v]))
                return ctx.shared_zero
            if e.method in ("union", "intersection", "difference") and len(e.args) == 1:
                return _lower_set_setop(ctx, e.obj, e.args[0], e.method, id(e))
            if e.method in ("discard", "remove") and len(e.args) == 1:
                key_v = _set_member_key(e.args[0])
                if e.method == "discard":
                    obj_v = _lower_expr(ctx, e.obj)
                    has_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", obj_v, key_v]))
                    pop_b = ctx.new_block("setdiscardpop")
                    end_b = ctx.new_block("setdiscardend")
                    ctx.emit(IRInstr("br.t", None, [has_v, pop_b.label, end_b.label]))
                    ctx.switch_to(pop_b)
                    obj_v2 = _lower_expr(ctx, e.obj)
                    ctx.emit(IRInstr("call", None, ["_abi_dict_pop", obj_v2, key_v]))
                    ctx.emit(IRInstr("br", None, [end_b.label]))
                    ctx.switch_to(end_b)
                else:
                    obj_v = _lower_expr(ctx, e.obj)
                    ctx.emit(IRInstr("call", None, ["_abi_dict_pop", obj_v, key_v]))
                return ctx.shared_zero
            if e.method == "copy" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                new_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", new_v, ["_abi_new_instance"]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_update", new_v, obj_v]))
                return new_v
            if e.method == "pop" and not e.args:
                # Remove and return an arbitrary member (the first live
                # key), raising KeyError if the set is empty.
                obj_v = _lower_expr(ctx, e.obj)
                keys_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", obj_v]))
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [keys_v, _LIST_LEN_OFF]))
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", len_v, [len_addr]))
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                nonempty = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.ne", nonempty, [len_v, zero]))
                ok_b = ctx.new_block("setpopok")
                empty_b = ctx.new_block("setpopempty")
                ctx.emit(IRInstr("br.t", None, [nonempty, ok_b.label, empty_b.label]))

                ctx.switch_to(empty_b)
                msg_name = ctx.mctx.intern_str("KeyError: 'pop from an empty set'")
                msg_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
                exc_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["KeyError"]]))
                ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
                ctx.emit(IRInstr("br", None, [ok_b.label]))

                ctx.switch_to(ok_b)
                idx0 = ctx.tmp(I64)
                ctx.emit(IRInstr("const", idx0, [0]))
                first_addr = _list_elem_addr(ctx, keys_v, idx0)
                first_key = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", first_key, [first_addr]))
                obj_v2 = _lower_expr(ctx, e.obj)
                ctx.emit(IRInstr("call", None, ["_abi_dict_pop", obj_v2, first_key]))
                return first_key
            raise LowerError(f"unsupported expr MethodCall (set.{e.method})")
        if obj_ty == "str":
            obj_v = _lower_expr(ctx, e.obj)
            if e.method == "encode":
                return _lower_str_to_byte_list(ctx, e.obj)
            no_arg_str_methods = {
                "upper": "_abi_str_upper", "lower": "_abi_str_lower",
                "strip": "_abi_str_strip", "capitalize": "_abi_str_capitalize",
                "lstrip": "_abi_str_lstrip", "rstrip": "_abi_str_rstrip",
                "swapcase": "_abi_str_swapcase", "title": "_abi_str_title",
                "splitlines": "_abi_str_splitlines",
                # casefold: no true Unicode casefolding here, same
                # simplification codegen.py's STR_METHOD_RUNTIME table
                # makes (maps to the identical lower-casing helper).
                "casefold": "_abi_str_lower",
            }
            one_arg_list_methods = {
                "partition": "_abi_str_partition", "rpartition": "_abi_str_rpartition",
            }
            no_arg_int_methods = {
                "isdigit": "_abi_str_isdigit", "isalpha": "_abi_str_isalpha",
                "isalnum": "_abi_str_isalnum", "islower": "_abi_str_islower",
                "isupper": "_abi_str_isupper", "isspace": "_abi_str_isspace",
            }
            one_arg_int_methods = {
                "find": "_abi_str_index_of", "count": "_abi_str_count",
                "startswith": "_abi_str_starts_with", "endswith": "_abi_str_ends_with",
            }
            one_arg_str_methods = {
                "zfill": "_abi_str_zfill", "removeprefix": "_abi_str_removeprefix",
                "removesuffix": "_abi_str_removesuffix",
            }
            pad_methods = {
                "ljust": "_abi_str_ljust",
                "rjust": "_abi_str_rjust",
                "center": "_abi_str_center",
            }
            if e.method in no_arg_str_methods and not e.args:
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, [no_arg_str_methods[e.method], obj_v]))
                return v
            if e.method in no_arg_int_methods and not e.args:
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, [no_arg_int_methods[e.method], obj_v]))
                return v
            if e.method in one_arg_int_methods and len(e.args) == 1:
                if A.expr_type(e.args[0]) != "str":
                    raise LowerError(f"unsupported expr MethodCall (str.{e.method} non-str arg)")
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, [one_arg_int_methods[e.method], obj_v, arg_v]))
                return v
            if e.method in one_arg_str_methods and len(e.args) == 1:
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, [one_arg_str_methods[e.method], obj_v, arg_v]))
                return v
            if e.method in one_arg_list_methods and len(e.args) == 1:
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, [one_arg_list_methods[e.method], obj_v, arg_v]))
                return v
            if e.method in pad_methods and len(e.args) in (1, 2):
                width_v = _lower_expr(ctx, e.args[0])
                if len(e.args) == 2:
                    fill_v = _lower_expr(ctx, e.args[1])
                else:
                    fill_name = ctx.mctx.intern_str(" ")
                    fill_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("global_addr", fill_v, [fill_name]))
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, [pad_methods[e.method], obj_v, width_v, fill_v]))
                return v
            if e.method == "split" and (not e.args or A.is_none_expr(e.args[0])):
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_split_ws", obj_v]))
                return v
            if e.method == "split" and len(e.args) in (1, 2):
                if A.expr_type(e.args[0]) != "str":
                    raise LowerError("unsupported expr MethodCall (str.split non-str sep)")
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_split", obj_v, arg_v]))
                return v
            if e.method == "rsplit" and len(e.args) == 2:
                if A.expr_type(e.args[0]) != "str":
                    raise LowerError("unsupported expr MethodCall (str.rsplit non-str sep)")
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_rsplit", obj_v, arg_v]))
                return v
            if e.method == "join" and len(e.args) == 1:
                if A.expr_type(e.args[0]) != "list":
                    raise LowerError("unsupported expr MethodCall (str.join non-list arg)")
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_join", obj_v, arg_v]))
                return v
            if e.method == "replace" and len(e.args) == 2:
                if A.expr_type(e.args[0]) != "str" or A.expr_type(e.args[1]) != "str":
                    raise LowerError("unsupported expr MethodCall (str.replace non-str arg)")
                old_v = _lower_expr(ctx, e.args[0])
                new_v = _lower_expr(ctx, e.args[1])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_replace", obj_v, old_v, new_v]))
                return v
            raise LowerError(f"unsupported expr MethodCall (str.{e.method})")
        if obj_ty.startswith("instance:"):
            # Walk the inheritance chain for the method's actual defining
            # class -- a static instance:Dog type doesn't mean Dog itself
            # defines every method; an inherited-but-not-overridden one
            # (e.g. a base class's @property with no subclass override)
            # is only ever emitted as Animal__greeting, never Dog__greeting.
            cls_name = obj_ty.split(":", 1)[1]
            owner = _resolve_method_owner(ctx, cls_name, e.method) or cls_name
            obj_v = _lower_expr(ctx, e.obj)
            args = [obj_v] + [_lower_expr(ctx, a) for a in e.args]
            res_ty = ir_type_for(A.expr_type(e))

            rows = _virtual_dispatch_rows(ctx, cls_name, e.method)
            owners: list[str] = []
            for _cid, ow in rows:
                if ow not in owners:
                    owners.append(ow)
            if len(owners) <= 1:
                # No subclass overrides this method -- bind statically.
                v = ctx.tmp(res_ty)
                ctx.emit(IRInstr("call", v, [f"{owner}__{e.method}", *args]))
                return v

            # Subclasses override this method: dispatch on the receiver's
            # runtime __class__ id (mirrors codegen.py's
            # _virtual_dispatch_rows call site exactly, just as a chain of
            # blocks instead of a chain of labels+jumps). Pre-create every
            # block up front (check_i / hit_i pairs, default, end) so each
            # br.t below always targets an already-known label -- no
            # forward-reference patching needed.
            key_sym = ctx.mctx.intern_str("__class__")
            key_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", key_v, [key_sym]))
            untagged = ctx.tmp(I64)
            ctx.emit(IRInstr("const", untagged, [-1]))
            class_id = ctx.tmp(I64)
            ctx.emit(IRInstr("call", class_id, ["_abi_dict_get_default", obj_v, key_v, untagged]))

            res_ptr = ctx.ensure_slot(f"__vdisp_res_{id(e)}", res_ty)
            other_owners = [ow for ow in owners if ow != owner]
            check_blocks = [ctx.new_block(f"vdispcheck{i}") for i in range(len(other_owners))]
            hit_blocks = [ctx.new_block(f"vdisphit{i}") for i in range(len(other_owners))]
            default_b = ctx.new_block("vdispdefault")
            end_b = ctx.new_block("vdispend")

            ctx.emit(IRInstr("br", None, [(check_blocks[0] if check_blocks else default_b).label]))

            for i, ow in enumerate(other_owners):
                ctx.switch_to(check_blocks[i])
                cid = next(c for c, o in rows if o == ow)
                cid_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", cid_v, [cid]))
                is_match = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", is_match, [class_id, cid_v]))
                next_label = check_blocks[i + 1].label if i + 1 < len(check_blocks) else default_b.label
                ctx.emit(IRInstr("br.t", None, [is_match, hit_blocks[i].label, next_label]))

                ctx.switch_to(hit_blocks[i])
                mv = ctx.tmp(res_ty)
                ctx.emit(IRInstr("call", mv, [f"{ow}__{e.method}", *args]))
                ctx.emit(IRInstr("store", None, [mv, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(default_b)
            dv = ctx.tmp(res_ty)
            ctx.emit(IRInstr("call", dv, [f"{owner}__{e.method}", *args]))
            ctx.emit(IRInstr("store", None, [dv, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(end_b)
            out = ctx.tmp(res_ty)
            ctx.emit(IRInstr("load", out, [res_ptr]))
            return out
        if obj_ty == "type" and isinstance(e.obj, A.Name) and e.obj.name in ctx.mctx.class_names:
            sym = f"{e.obj.name}__{e.method}"
            is_static = _resolved_method_is_static(ctx, e.obj.name, e.method)
            call_args = [_lower_expr(ctx, a) for a in e.args]
            args = call_args if is_static else [ctx.shared_zero] + call_args
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [sym, *args]))
            return v
        # Unknown method on an opaque/any-typed receiver: evaluate receiver
        # and args for side effects and return 0.  Mirrors codegen.py's
        # graceful stub so selfhost builds survive unmodeled FFI methods.
        _lower_expr(ctx, e.obj)
        for _arg in e.args:
            _lower_expr(ctx, _arg)
        zero_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_v, [0]))
        return zero_v

    if isinstance(e, A.Attr):
        if (
            e.name == "__name__"
            and isinstance(e.obj, A.Call)
            and e.obj.func == "type"
            and len(e.obj.args) == 1
        ):
            return _lower_type_name_attr(ctx, e)
        if isinstance(e.obj, A.Name) and (e.obj.name, e.name) in ctx.mctx.class_var_labels:
            # `ClassName.attr` (a plain class's own static class-level
            # variable, e.g. `class Config: version = 5`) -- reads the
            # dedicated `__cv_<Class>__<attr>` global lower_module()
            # registers and initializes at startup (mirrors codegen.py's
            # class_var_labels convention exactly). Previously entirely
            # unhandled here: `e.obj` (`Config`) is a class name, never
            # bound to any real variable/slot, so `_lower_expr(ctx, e.obj)`
            # fell through to `A.Name`'s "class name used as a value" case
            # and returned the class's raw numeric RTTI id (e.g. 0) --
            # which then got used as if it were a real dict/instance
            # POINTER for the generic attribute fallback below, crashing
            # (confirmed via gdb: SIGSEGV dereferencing near address 0,
            # i.e. whatever small integer the class's RTTI id happened to
            # be). `sema.py`'s own Attr check (~line 6602) has the matching
            # "class-level variable read" branch but only sets the node's
            # *type*; it never called `_check_expr` on `e.obj` either, so
            # `e.obj.inferred_type` was left at the parser's placeholder
            # default too -- both sides of this bug independently trace
            # back to "a node's own fields were read without the node ever
            # going through normal type-checking/lowering."
            label = ctx.mctx.class_var_labels[(e.obj.name, e.name)]
            ty = ctx.mctx.global_types.get(label, ir_type_for(A.expr_type(e)))
            ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", ptr, [label]))
            v = ctx.tmp(ty)
            ctx.emit(IRInstr("load", v, [ptr]))
            return v
        if (
            isinstance(e.obj, A.Name)
            and e.obj.name in ctx.mctx.imported_modules
            and e.name not in ctx.mctx.global_types
        ):
            # `module.CONST` on a pure-FFI-binding-table module (e.g.
            # `os.sep`, `math.pi`) -- the previous branch below only
            # covers modules whose top-level values got hoisted as real
            # `Assign` statements by program.py's whole-program merge
            # (`string.py`'s `ascii_lowercase`); a binding-table `Const`
            # entry (`os.py`'s `BINDINGS = {"sep": Const(ty="str",
            # value="/", value_windows="\\"), ...}`) never becomes a real
            # global at all, so it fell all the way through to the
            # generic instance-attribute fallback below, which treated
            # the module name (never bound to any real variable) as an
            # uninitialized stack slot read as a dict/instance pointer --
            # a guaranteed segfault (confirmed via gdb, same crash shape
            # as the `string.ascii_lowercase` bug this mirrors). Mirrors
            # codegen.py's `_gen_const_load`/`_platform_const_value`
            # exactly: a `Const`'s value is resolved at COMPILE time (the
            # binding table itself IS the "global", there's nothing to
            # load from memory), with the same `value_windows` override
            # convention (this backend currently only targets Windows PE
            # output, so unconditionally prefer it when present, matching
            # target_windows.py's `_platform_const_value`).
            b = ctx.mctx.imported_modules[e.obj.name].get(e.name)
            if b is not None and not hasattr(b, "arg_types"):  # a Const, not a Func
                value = getattr(b, "value_windows", None)
                if value is None:
                    value = getattr(b, "value", None)
                if b.ty == "str" and isinstance(value, str):
                    name = ctx.mctx.intern_str(value)
                    v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("global_addr", v, [name]))
                    return v
                if b.ty == "int" and isinstance(value, (int, bool)):
                    v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", v, [int(value)]))
                    return v
                if b.ty == "float" and isinstance(value, (int, float)):
                    v = ctx.tmp(F64)
                    ctx.emit(IRInstr("const", v, [float(value)]))
                    return v
                # Anything else (list-typed sys.argv/sys.path sentinels,
                # etc.) falls through unchanged -- a separate, smaller,
                # not-yet-scoped gap, same as before this fix.
        if isinstance(e.obj, A.Name) and A.expr_type(e.obj) == "module" and e.name in ctx.mctx.global_types:
            # `module.NAME` (e.g. `string.ascii_lowercase`) -- a merged
            # stdlib module is a compile-time-only namespace, not a real
            # runtime value; `e.obj` (the module name) was never bound to
            # any variable/slot at all. Falling through to the generic
            # instance-attribute path below (which treats `_lower_expr(ctx,
            # e.obj)` as a real dict/instance pointer) read an uninitialized
            # stack slot as that pointer and called _abi_dict_get_default on
            # garbage -- a guaranteed segfault, confirmed via gdb (crash
            # inside msvcrt.dll after reading uninitialized stack memory as
            # a dict header). `program.py`'s whole-program merge already
            # hoists a merged module's top-level values as plain globals
            # under their bare name (no module-name prefix -- see the
            # IRGlobal entries `lower_module` emits for e.g. `string.py`'s
            # `ascii_lowercase`), so this is just a direct global read --
            # but ONLY when `e.name` is actually a real materialized global
            # (`ctx.mctx.global_types` check): a pure-FFI-binding module
            # like `math.py` (whose `pi`/`sqrt`/etc. are `Const`/`Func`
            # binding-table entries, never real `Assign` statements
            # `program.py` would hoist) has no such global at all, and
            # `math.pi`-style access is a separate, pre-existing,
            # not-yet-fixed gap (ir_lower.py has no `ffi_consts` handling
            # for either `A.Name` or `A.Attr` today) -- falls through to
            # the generic path below unchanged for that case, same
            # (already broken) behavior as before this fix, not worse.
            ty = ctx.mctx.global_types[e.name]
            ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", ptr, [e.name]))
            v = ctx.tmp(ty)
            ctx.emit(IRInstr("load", v, [ptr]))
            return v
        # obj.name -> _abi_dict_get_default(obj, name, default=0). Instances
        # are runtime dicts keyed by field name; bridges to the existing,
        # tested _runtime_dict_get_default via the ABI shim (see
        # build/abi_shims.asm), since that helper's own calling convention
        # (rax/rbx/rcx) predates this ABI-compliant IR pipeline.
        obj_val = _lower_expr(ctx, e.obj)
        name = ctx.mctx.intern_str(e.name)
        key_ptr = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", key_ptr, [name]))
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_val, key_ptr, zero]))
        if A.expr_type(e) == "float":
            # Every dict/instance-attribute cell is a plain 8-byte int slot
            # (_abi_dict_set/get_default only ever move GP-sized values);
            # a float attribute's bits went in via bitcast_f2i on write
            # (see A.AttrAssign below) and must come back out the same way,
            # not as a numeric int->float conversion (sitofp would treat
            # the raw bit pattern as an integer value, corrupting it).
            fv = ctx.tmp(F64)
            ctx.emit(IRInstr("bitcast_i2f", fv, [v]))
            return fv
        return v

    if isinstance(e, A.Call) and e.func in ctx.mctx.class_names:
        if e.func == "Thread":
            return _lower_thread_ctor(ctx, e)
        v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", v, ["_abi_new_instance"]))
        cid = ctx.mctx.class_ids.get(e.func)
        if cid is not None:
            key_name = ctx.mctx.intern_str("__class__")
            key_v = ctx.tmp(PTR)
            cid_v = ctx.tmp(I64)
            ctx.emit(IRInstr("global_addr", key_v, [key_name]))
            ctx.emit(IRInstr("const", cid_v, [cid]))
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", v, key_v, cid_v]))
        owner = _resolve_method_owner(ctx, e.func, "__init__")
        if owner is not None:
            init_args = [v]
            for arg in e.args:
                init_args.append(_lower_expr(ctx, arg))
            ctx.emit(IRInstr("call", None, [f"{owner}____init__", *init_args]))
        return v

    if isinstance(e, A.Call) and e.func in ctx.mctx.ffi_funcs:
        # A bound stdlib FFI function (e.g. asmlib.hardware.in_byte/cpuid/
        # disable_interrupts): call its real c_name symbol, not the
        # asmpython-level name. All of hardware.py's bindings take plain
        # int args (no float, no >4-arg overflow), which is exactly what a
        # normal "call" IR op already marshals -- the same standard-ABI
        # argument passing _gen_ffi_call does by hand in the legacy
        # codegen.py for the same bindings.
        fn = ctx.mctx.ffi_funcs[e.func]
        c_name = getattr(fn, "c_name_windows", None) or fn.c_name
        args = [_lower_expr(ctx, a) for a in e.args]
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", v, [c_name] + args))
        return v

    if isinstance(e, A.Call):
        if e.func == "str" and len(e.args) == 1:
            # Delegates to _lower_expr_as_str, the general str-coercion
            # helper f-strings/print() already use -- it covers bool/None
            # (this hand-rolled version used to fall through to plain
            # decimal conversion for True/False, printing "1"/"0") plus
            # tuple/list/dict/set, which this call site never supported at
            # all.
            return _lower_expr_as_str(ctx, e.args[0])
        if e.func == "int" and len(e.args) in (1, 2):
            arg = e.args[0]
            arg_t = A.expr_type(arg)
            if len(e.args) == 2:
                str_v = _lower_expr(ctx, arg)
                base_v = _lower_expr(ctx, e.args[1])
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("call", out, ["_abi_str_to_int_base", str_v, base_v]))
                return out
            if arg_t == "str":
                str_v = _lower_expr(ctx, arg)
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("call", out, ["_abi_str_to_int", str_v]))
                return out
            if arg_t == "float":
                float_v = _lower_expr(ctx, arg)
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("fptosi", out, [float_v]))
                return out
            if arg_t.startswith("instance:"):
                owner = _resolve_method_owner(ctx, arg_t.split(":", 1)[1], "__int__")
                if owner is not None:
                    obj_v = _lower_expr(ctx, arg)
                    out = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", out, [f"{owner}____int__", obj_v]))
                    return out
            return _lower_expr(ctx, arg)
        if e.func == "sorted" and len(e.args) == 1:
            return _lower_sorted(ctx, e)
        if e.func == "type" and len(e.args) == 1:
            arg = e.args[0]
            arg_t = A.expr_type(arg)
            _lower_expr(ctx, arg)
            if arg_t == "int" and A.is_bool_expr(arg):
                text = "<class 'bool'>"
            elif arg_t == "int" and A.is_none_expr(arg):
                text = "<class 'NoneType'>"
            elif arg_t in ("int", "float", "str", "list", "dict", "tuple", "set"):
                text = f"<class '{arg_t}'>"
            elif arg_t.startswith("instance:"):
                text = f"<class '__main__.{arg_t.split(':', 1)[1]}'>"
            else:
                text = ""
            sym = ctx.mctx.intern_str(text)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", out, [sym]))
            return out
        if e.func == "chr" and len(e.args) == 1:
            n_v = _lower_expr(ctx, e.args[0])
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_chr", n_v]))
            return out
        if e.func == "repr" and len(e.args) == 1:
            return _lower_expr_as_str(ctx, e.args[0], repr_mode=True)
        if e.func == "reversed" and len(e.args) == 1:
            src_v = _lower_expr(ctx, e.args[0])
            start_v = ctx.tmp(I64)
            stop_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", start_v, [-9223372036854775808]))
            ctx.emit(IRInstr("const", stop_v, [9223372036854775807]))
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", v, ["_abi_list_slice", src_v, start_v, stop_v]))
            ctx.emit(IRInstr("call", None, ["_abi_list_reverse", v]))
            return v
        if e.func == "round" and len(e.args) == 1:
            arg_t = A.expr_type(e.args[0])
            if arg_t == "float":
                f_v = _lower_expr(ctx, e.args[0])
                rounded = ctx.tmp(F64)
                ctx.emit(IRInstr("call", rounded, ["_abi_round_f64", f_v]))
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("fptosi", out, [rounded]))
                return out
            # round(int) / round(bool) is the identity.
            return _lower_expr(ctx, e.args[0])
        if e.func in ("hex", "oct", "bin") and len(e.args) == 1:
            n_v = _lower_expr(ctx, e.args[0])
            base = ctx.tmp(I64)
            ctx.emit(IRInstr("const", base, [{"hex": 16, "oct": 8, "bin": 2}[e.func]]))
            prefix_name = ctx.mctx.intern_str({"hex": "0x", "oct": "0o", "bin": "0b"}[e.func])
            prefix_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", prefix_v, [prefix_name]))
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_int_to_base", n_v, base, prefix_v]))
            return out
        if e.func == "divmod" and len(e.args) == 2:
            a_v = _lower_expr(ctx, e.args[0])
            b_v = _lower_expr(ctx, e.args[1])
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_divmod", a_v, b_v]))
            return out
        if e.func == "pow" and len(e.args) == 2:
            # pow(base, exp) -- ints only take the loop; a float either
            # side goes through the earlier A.BinOp-style libc `pow` call
            # via the "**" operator's own dispatch, not this builtin-call
            # site (arg type mismatch is exactly what made this path
            # necessary in the first place -- see _lower_int_pow).
            base_v = _lower_expr(ctx, e.args[0])
            exp_v = _lower_expr(ctx, e.args[1])
            return _lower_int_pow(ctx, base_v, exp_v, id(e))
        if e.func == "bool" and len(e.args) == 1:
            arg = e.args[0]
            arg_t = A.expr_type(arg)
            val = _lower_expr(ctx, arg)
            if arg_t.startswith("instance:"):
                cls_name = arg_t.split(":", 1)[1]
                owner = _resolve_method_owner(ctx, cls_name, "__bool__")
                method = "__bool__"
                if owner is None:
                    owner = _resolve_method_owner(ctx, cls_name, "__len__")
                    method = "__len__"
                if owner is not None:
                    v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", v, [f"{owner}__{method}", val]))
                    return _value_truthy(ctx, v)
                return _value_truthy(ctx, val)
            if arg_t in ("list", "tuple", "dict", "set", "str"):
                # A container/str value can be a NULL pointer (an Optional
                # holding None); None is falsy too, so skip the
                # length/first-byte read when val is already NULL --
                # reading through a NULL pointer here would segfault.
                zero = ctx.tmp(PTR)
                ctx.emit(IRInstr("const", zero, [0]))
                is_null = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", is_null, [val, zero]))
                null_b = ctx.new_block("boolnull")
                live_b = ctx.new_block("boollive")
                end_b = ctx.new_block("boolend")
                res_ptr = ctx.ensure_slot(f"__bool_res_{id(e)}", I64)
                ctx.emit(IRInstr("br.t", None, [is_null, null_b.label, live_b.label]))
                ctx.switch_to(null_b)
                fz = ctx.tmp(I64)
                ctx.emit(IRInstr("const", fz, [0]))
                ctx.emit(IRInstr("store", None, [fz, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))
                ctx.switch_to(live_b)
                if arg_t == "str":
                    ch = ctx.tmp(U8)
                    ctx.emit(IRInstr("load", ch, [val]))
                    count_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("zext", count_v, [ch]))
                else:
                    len_addr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", len_addr, [val, _LIST_LEN_OFF]))
                    count_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", count_v, [len_addr]))
                cz = ctx.tmp(I64)
                ctx.emit(IRInstr("const", cz, [0]))
                nz = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.ne", nz, [count_v, cz]))
                ctx.emit(IRInstr("store", None, [nz, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))
                ctx.switch_to(end_b)
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("load", out, [res_ptr]))
                return out
            return _value_truthy(ctx, val)
        if e.func == "input" and len(e.args) in (0, 1):
            if e.args:
                prompt_v = _lower_expr_as_str(ctx, e.args[0])
                fmt_name = ctx.mctx.intern_str("%s")
                fmt_ptr = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", fmt_ptr, [fmt_name]))
                ctx.emit(IRInstr("call", None, ["printf", fmt_ptr, prompt_v]))
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_input"]))
            return out
        call_owner = getattr(e, "dunder_call_owner", None)
        if call_owner is not None:
            # `obj(...)` where `obj` is a variable holding an instance with
            # a real `__call__` method (e.g. `add5 = Adder(5); add5(3)`).
            # Without this check, this fell through to the plain-name-call
            # path below, which treats `e.func` (here just the variable
            # name `add5`) as either a class name or a callable slot --
            # neither matches, so it ended up trying to CALL the instance's
            # own dict/struct pointer as if it were a code address. Confirmed
            # crashing via a minimal repro (`class Adder: __call__` style).
            obj_v = _lower_expr(ctx, A.Name(name=e.func, pos=e.pos))
            args = [obj_v] + [_lower_expr(ctx, a) for a in e.args]
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [f"{call_owner}____call__", *args]))
            return v
        args = [_lower_expr(ctx, a) for a in e.args]
        v = ctx.tmp(ir_type_for(A.expr_type(e)))
        # A call through a plain variable (not a real function/class name)
        # -- e.g. `double = lambda x: x*2; double(21)`, or a lambda/func
        # passed in as a parameter (`def apply(g, v): return g(v)`). This
        # must check BOTH local slots (ctx.slot_ty) and module globals
        # (ctx.mctx.global_types), not just locals: a module-scope
        # `name = lambda ...` never gets a local slot at all (it's a real
        # global, per _is_global_name), so the old locals-only check fell
        # through to the plain-symbol-call branch below with the bare
        # variable name as the call target -- linking against a
        # nonexistent symbol `double` instead of loading the function
        # pointer the global actually holds. Confirmed via gdb: segfault
        # at the very first lambda-call test case, before even reaching
        # user code.
        is_callable_var = (
            e.func not in ctx.mctx.func_names
            and e.func not in ctx.mctx.class_names
            and (e.func in ctx.slot_ty or e.func in ctx.mctx.global_types)
        )
        if is_callable_var:
            target = _lower_expr(ctx, A.Name(name=e.func, pos=e.pos))
            ctx.emit(IRInstr("call", v, [target, *args]))
        else:
            ctx.emit(IRInstr("call", v, [e.func, *args]))
        return v

    raise LowerError(f"unsupported expr {type(e).__name__}")


def _lower_stmt(ctx: _FuncCtx, s: A.Stmt) -> None:
    if isinstance(s, A.ConstDecl):
        # Normalize at entry rather than duplicating Assign's lowering in a
        # parallel branch: any future bugfix/feature added to A.Assign's
        # lowering automatically covers ConstDecl too, with zero risk of the
        # two paths drifting apart. Sema has already enforced the const-lock
        # semantics by this point (ir_lower never sees a rebinding of a
        # const name) -- ConstDecl lowers to exactly the same store as an
        # ordinary initialized assignment.
        s = A.Assign(target=s.name, value=s.value, pos=s.pos, annot=s.annotation)

    if isinstance(s, A.Pass):
        return

    if isinstance(s, A.Global) or isinstance(s, A.Nonlocal):
        return

    if isinstance(s, A.Assign):
        val = _lower_expr(ctx, s.value)
        ptr = _name_ptr(ctx, s.target, ctx.mctx.global_types.get(s.target, val.type))
        ctx.emit(IRInstr("store", None, [val, ptr]))
        if not _is_global_name(ctx, s.target) and A.expr_type(s.value) == "list":
            ctx.slot_el_ty[s.target] = getattr(s.value, "list_el_type", "int")
        return

    if isinstance(s, A.AugAssign):
        # `target op= value` -> `target = target op value`, same int/float
        # binop dispatch as a plain BinOp (the target's current static type
        # comes from its existing slot, defaulting to int for a first write
        # -- ensure_slot below mirrors A.Assign's own untyped-slot default).
        cur_ty = ctx.mctx.global_types.get(s.target, ctx.slot_ty.get(s.target, I64))
        ptr = _name_ptr(ctx, s.target, cur_ty)
        cur = ctx.tmp(cur_ty)
        ctx.emit(IRInstr("load", cur, [ptr]))
        rhs_ty = A.expr_type(s.value)
        rhs = _lower_expr(ctx, s.value)
        if s.op == "|" and rhs_ty in ("dict", "set"):
            # `d |= other` (PEP 584) / `s |= other`: merge other's entries
            # into the target in place -- the header pointer itself
            # doesn't change, unlike the fresh-dict `|` BinOp above.
            ctx.emit(IRInstr("call", None, ["_abi_dict_update", cur, rhs]))
            return
        if s.op == "+" and cur_ty is PTR and A.expr_type(s.value) == "list":
            # `xs += other` (list): extend xs IN PLACE (same header pointer
            # -- no rebind needed) rather than the plain-BinOp `+` case's
            # fresh-copy-then-extend, matching codegen.py's `_runtime_list_
            # extend`-only convention for AugAssign specifically. Before
            # this fix, AugAssign had no list-aware branch at all and fell
            # through to the plain-int `_BINOP["+"]` path at the bottom,
            # OR-ing/adding the two raw list header pointers together as
            # integers -- corrupts the slot on the very next read.
            ctx.emit(IRInstr("call", None, ["_abi_list_extend", cur, rhs]))
            return
        if s.op == "+" and cur_ty is PTR and rhs_ty == "str":
            # `s += other` (str): immutable -- concat to a NEW pointer and
            # rebind the slot, unlike the list case just above.
            res = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", res, ["_abi_str_concat", cur, rhs]))
            ctx.emit(IRInstr("store", None, [res, ptr]))
            return
        if cur_ty is F64 or rhs_ty == "float":
            if s.op not in _FBINOP and s.op not in ("%", "**"):
                raise LowerError(f"unsupported float augassign op {s.op!r}")
            if rhs_ty != "float":
                # e.g. `x_float += 1`: promote the int RHS before fadd, same
                # as a plain BinOp -- fadd on a mismatched i64 operand would
                # silently read garbage (int and float share no bit layout).
                rhs_f = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", rhs_f, [rhs]))
                rhs = rhs_f
            res = ctx.tmp(F64)
            if s.op in ("%", "**"):
                c_name = "fmod" if s.op == "%" else "pow"
                ctx.emit(IRInstr("call", res, [c_name, cur, rhs]))
            else:
                ctx.emit(IRInstr(_FBINOP[s.op], res, [cur, rhs]))
            if cur_ty is not F64:
                # First write was int but this op promotes to float -- widen
                # the slot itself going forward isn't supported (slots are
                # fixed-type); reject rather than silently truncate back.
                raise LowerError("augassign int->float promotion needs a float-declared target")
        elif s.op in ("//", "%"):
            # See _lower_int_floordivmod's docstring -- plain idiv/irem
            # truncate toward zero, // and % need floor-toward-(-inf).
            res = _lower_int_floordivmod(ctx, cur, rhs, s.op, id(s))
            ctx.emit(IRInstr("store", None, [res, ptr]))
            return
        else:
            if s.op not in _BINOP:
                raise LowerError(f"unsupported augassign op {s.op!r}")
            res = ctx.tmp(I64)
            ctx.emit(IRInstr(_BINOP[s.op], res, [cur, rhs]))
        ctx.emit(IRInstr("store", None, [res, ptr]))
        return

    if isinstance(s, A.IndexAssign):
        target = s.target
        if isinstance(target.index, A.Slice):
            obj_ty = A.expr_type(target.obj)
            if obj_ty != "list":
                raise LowerError(f"unsupported stmt IndexAssign (slice {obj_ty})")
            sl = target.index
            if sl.step is not None:
                raise LowerError("unsupported stmt IndexAssign (slice step)")
            if A.expr_type(s.value) != "list":
                raise LowerError("unsupported stmt IndexAssign (slice non-list value)")
            dst_v = _lower_expr(ctx, target.obj)
            src_v = _lower_expr(ctx, s.value)
            if sl.start is None:
                start_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", start_v, [-9223372036854775808]))
            else:
                start_v = _lower_expr(ctx, sl.start)
            if sl.stop is None:
                stop_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", stop_v, [9223372036854775807]))
            else:
                stop_v = _lower_expr(ctx, sl.stop)
            ctx.emit(IRInstr("call", None, ["_abi_list_slice_assign", dst_v, start_v, stop_v, src_v]))
            return
        obj_ty = A.expr_type(target.obj)
        if obj_ty == "dict":
            obj_v = _lower_expr(ctx, target.obj)
            key_v = _lower_dict_key(ctx, target.index)
            val = _lower_expr(ctx, s.value)
            if A.expr_type(s.value) == "float":
                # Same int-only-cell constraint as A.AttrAssign/A.ListLit.
                iv = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
                val = iv
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_v, val]))
            return
        if obj_ty != "list":
            raise LowerError(f"unsupported stmt IndexAssign ({obj_ty})")
        obj_v = _lower_expr(ctx, target.obj)
        idx_v = _lower_expr(ctx, target.index)
        addr = _list_elem_addr(ctx, obj_v, idx_v)
        val = _lower_expr(ctx, s.value)
        ctx.emit(IRInstr("store", None, [val, addr]))
        return

    if isinstance(s, A.TupleAssign):
        if len(s.values) == len(s.targets) and not any(
            isinstance(t, A.StarTarget) for t in s.targets
        ):
            # Parallel form (a, b = 1, 2 / a, b = b, a / xs[i], xs[j] =
            # xs[j], xs[i] / p.x, p.y = p.y, p.x): every rhs is evaluated
            # into a temp *before* any store, so swaps work. Unlike the
            # single-iterable unpack form below, Subscript/Attr targets
            # are allowed here (sema only permits plain names for
            # single-iterable unpack -- see TupleAssign's docstring).
            vals = [_lower_expr(ctx, v) for v in s.values]
            for target, val in zip(s.targets, vals):
                _store_tuple_assign_target(ctx, target, val)
            return
        if any(isinstance(t, A.StarTarget) for t in s.targets):
            if len(s.values) != 1 or A.expr_type(s.values[0]) != "list":
                raise LowerError("unsupported stmt TupleAssign (starred target shape)")
            if not all(isinstance(t, (A.Name, A.StarTarget)) for t in s.targets):
                raise LowerError("unsupported stmt TupleAssign (starred target, non-Name target)")
            # `a, *rest = xs` / `*init, last = xs` / `a, *mid, b = xs`
            # (xs: list[T]). Evaluate xs once; plain targets before the
            # star read xs[0..n_before-1], plain targets after the star
            # read from the end (xs[len-n_after..len-1]), and the star
            # target becomes _abi_list_slice(xs, n_before, len - n_after)
            # -- mirrors codegen.py's TupleAssign-with-StarTarget handling.
            star_i = next(i for i, t in enumerate(s.targets) if isinstance(t, A.StarTarget))
            n_before = star_i
            n_after = len(s.targets) - star_i - 1
            el_ty = _iter_element_type(s.values[0])
            src_v = _lower_expr(ctx, s.values[0])
            src_ptr = ctx.ensure_slot(f"__tupunpack_{id(s)}", PTR)
            ctx.emit(IRInstr("store", None, [src_v, src_ptr]))

            for i in range(n_before):
                cur_src = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", cur_src, [src_ptr]))
                idx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", idx_v, [i]))
                addr = _list_elem_addr(ctx, cur_src, idx_v)
                val = ctx.tmp(ir_type_for(el_ty))
                ctx.emit(IRInstr("load", val, [addr]))
                _store_loop_target(ctx, s.targets[i].name, val, el_ty)

            for j in range(n_after):
                cur_src2 = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", cur_src2, [src_ptr]))
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [cur_src2, _LIST_LEN_OFF]))
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", len_v, [len_addr]))
                n_after_j = ctx.tmp(I64)
                ctx.emit(IRInstr("const", n_after_j, [n_after - j]))
                idx_v2 = ctx.tmp(I64)
                ctx.emit(IRInstr("isub", idx_v2, [len_v, n_after_j]))
                addr2 = _list_elem_addr(ctx, cur_src2, idx_v2)
                val2 = ctx.tmp(ir_type_for(el_ty))
                ctx.emit(IRInstr("load", val2, [addr2]))
                _store_loop_target(ctx, s.targets[star_i + 1 + j].name, val2, el_ty)

            rest_src = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", rest_src, [src_ptr]))
            rest_len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", rest_len_addr, [rest_src, _LIST_LEN_OFF]))
            rest_len = ctx.tmp(I64)
            ctx.emit(IRInstr("load", rest_len, [rest_len_addr]))
            n_after_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", n_after_v, [n_after]))
            stop_v = ctx.tmp(I64)
            ctx.emit(IRInstr("isub", stop_v, [rest_len, n_after_v]))
            start_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", start_v, [n_before]))
            rest_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", rest_v, ["_abi_list_slice", rest_src, start_v, stop_v]))
            _store_loop_target(ctx, s.targets[star_i].name, rest_v, "list")
            return
        if not all(isinstance(t, A.Name) for t in s.targets):
            raise LowerError("unsupported stmt TupleAssign (non-Name target)")
        names = [t.name for t in s.targets]
        if len(s.values) == 1 and A.expr_type(s.values[0]) in ("list", "tuple"):
            # Single-iterable unpack (a, b = some_tuple_or_list_expr).
            src_v = _lower_expr(ctx, s.values[0])
            for i, name in enumerate(names):
                idx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", idx_v, [i]))
                addr = _list_elem_addr(ctx, src_v, idx_v)
                elem_ty = I64
                val = ctx.tmp(elem_ty)
                ctx.emit(IRInstr("load", val, [addr]))
                ptr = _name_ptr(ctx, name, ctx.mctx.global_types.get(name, elem_ty))
                ctx.emit(IRInstr("store", None, [val, ptr]))
            return
        raise LowerError("unsupported stmt TupleAssign (shape)")

    if isinstance(s, A.AttrAssign):
        if isinstance(s.obj, A.Name) and (s.obj.name, s.name) in ctx.mctx.class_var_labels:
            # `ClassName.attr = value` -- writes the dedicated
            # `__cv_<Class>__<attr>` global directly. See the matching
            # A.Attr READ-side branch's comment for the full story; this is
            # its write-side counterpart, needed for the same reason (e.g.
            # `Config.version = 10` after reading `Config.version` earlier
            # in the same test).
            label = ctx.mctx.class_var_labels[(s.obj.name, s.name)]
            val = _lower_expr(ctx, s.value)
            ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", ptr, [label]))
            ctx.emit(IRInstr("store", None, [val, ptr]))
            return
        # obj.name = value -> _abi_dict_set(obj, name, value); see the
        # A.Attr read path's comment for why this goes through a shim.
        obj_val = _lower_expr(ctx, s.obj)
        name = ctx.mctx.intern_str(s.name)
        key_ptr = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", key_ptr, [name]))
        val = _lower_expr(ctx, s.value)
        if A.expr_type(s.value) == "float":
            # Bitcast the float's raw bits into a GP-sized value before the
            # shim call -- _abi_dict_set's own calling convention only
            # moves int-sized args, and the "call" IR op would otherwise
            # route an F64-typed value through an XMM argument register,
            # which _abi_dict_set never reads. See the A.Attr read path's
            # matching bitcast_i2f for the reverse.
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
            val = iv
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_val, key_ptr, val]))
        return

    if isinstance(s, A.Return):
        if s.value is None:
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            ret_val = zero
        else:
            ret_val = _lower_expr(ctx, s.value)
        # Restore any enclosing try-block exception handlers before returning,
        # innermost first.  Without this the stale handler pointer left in
        # _runtime_handler_top makes a later `raise` longjmp into a dead frame.
        n: int = len(ctx.try_handler_stack)
        i: int = n - 1
        while i >= 0:
            slot_name: str = ctx.try_handler_stack[i]
            parent_ptr = ctx.slot[slot_name]
            parent_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", parent_v, [parent_ptr]))
            _store_global(ctx, "_runtime_handler_top", parent_v)
            i = i - 1
        ctx.emit(IRInstr("ret", None, [ret_val]))
        return

    if isinstance(s, A.ExprStmt):
        _lower_expr(ctx, s.expr)
        return

    if isinstance(s, A.With):
        cm_v = _lower_expr(ctx, s.expr)
        if s.name is not None:
            cm_ty = A.expr_type(s.expr)
            ptr = _name_ptr(ctx, s.name, ctx.mctx.global_types.get(s.name, ir_type_for(cm_ty)))
            ctx.emit(IRInstr("store", None, [cm_v, ptr]))
        for st in s.body:
            _lower_stmt(ctx, st)
        return

    if isinstance(s, A.If):
        cond = _lower_truthy(ctx, s.test)
        then_b = ctx.new_block("then")
        else_b = ctx.new_block("else")
        merge_b = ctx.new_block("endif")
        ctx.emit(IRInstr("br.t", None, [cond, then_b.label, else_b.label]))

        ctx.switch_to(then_b)
        for st in s.then:
            _lower_stmt(ctx, st)
        ctx.emit(IRInstr("br", None, [merge_b.label]))

        ctx.switch_to(else_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
        ctx.emit(IRInstr("br", None, [merge_b.label]))

        ctx.switch_to(merge_b)
        return

    if isinstance(s, A.While):
        head_b = ctx.new_block("whilehead")
        body_b = ctx.new_block("whilebody")
        # `break` must skip the `else` clause entirely (Python's for/while-
        # else only runs on natural exhaustion, never on break) -- so
        # break's target (end_b, pushed onto loop_stack below) has to be a
        # separate block placed AFTER orelse's, not the same block orelse
        # itself runs in. natural_b is where the condition-false edge goes;
        # it falls through into orelse then into end_b, while break jumps
        # straight to end_b, bypassing orelse. Mirrors codegen.py's
        # top/nat/end three-label design for A.While exactly.
        natural_b = ctx.new_block("whilenatural") if s.orelse else None
        end_b = ctx.new_block("whileend")

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        cond = _lower_truthy(ctx, s.test)
        false_target = natural_b.label if natural_b is not None else end_b.label
        ctx.emit(IRInstr("br.t", None, [cond, body_b.label, false_target]))

        ctx.switch_to(body_b)
        ctx.loop_stack.append((head_b.label, end_b.label))
        for st in s.body:
            _lower_stmt(ctx, st)
        ctx.loop_stack.pop()
        ctx.emit(IRInstr("br", None, [head_b.label]))

        if natural_b is not None:
            ctx.switch_to(natural_b)
            for st in s.orelse:
                _lower_stmt(ctx, st)
            ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        return

    if isinstance(s, A.For) and s.iter is not None:
        if (
            s.targets
            and isinstance(s.iter, A.Call)
            and s.iter.func == "enumerate"
            and len(s.iter.args) == 1
            and len(s.targets) == 2
        ):
            src_e = s.iter.args[0]
            src_t = A.expr_type(src_e)
            if src_t not in ("list", "tuple", "str", "any"):
                raise LowerError(f"unsupported stmt For (enumerate {src_t!r})")
            elem_ty = _iter_element_type(src_e)
            if elem_ty == "float":
                raise LowerError("unsupported stmt For (enumerate float elements)")
            if src_t == "str":
                list_v = _lower_expr(ctx, src_e)
            else:
                list_v = _lower_expr(ctx, src_e)
            list_ptr = ctx.ensure_slot(f"__for_iter_{id(s)}", PTR)
            ctx.emit(IRInstr("store", None, [list_v, list_ptr]))
            idx_ptr = ctx.ensure_slot(f"__for_idx_{id(s)}", I64)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

            head_b = ctx.new_block("forenumhead")
            body_b = ctx.new_block("forenumbody")
            cont_b = ctx.new_block("forenumcont")
            # break-must-skip-else design, see A.While above.
            natural_b = ctx.new_block("forenumnatural") if s.orelse else None
            end_b = ctx.new_block("forenumend")

            ctx.emit(IRInstr("br", None, [head_b.label]))
            ctx.switch_to(head_b)
            idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
            cur_list_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", cur_list_v, [list_ptr]))
            if src_t == "str":
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", len_v, ["strlen", cur_list_v]))
            else:
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [cur_list_v, _LIST_LEN_OFF]))
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", len_v, [len_addr]))
            cond = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.lt", cond, [idx_v, len_v]))
            false_target = natural_b.label if natural_b is not None else end_b.label
            ctx.emit(IRInstr("br.t", None, [cond, body_b.label, false_target]))

            ctx.switch_to(body_b)
            body_list_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", body_list_v, [list_ptr]))
            body_idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", body_idx_v, [idx_ptr]))
            _store_loop_target(ctx, s.targets[0], body_idx_v, "int")
            if src_t == "str":
                elem_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", elem_v, ["_abi_str_char_at", body_list_v, body_idx_v]))
            else:
                addr = _list_elem_addr(ctx, body_list_v, body_idx_v)
                elem_v = ctx.tmp(ir_type_for(elem_ty))
                ctx.emit(IRInstr("load", elem_v, [addr]))
            _store_loop_target(ctx, s.targets[1], elem_v, elem_ty)

            ctx.loop_stack.append((cont_b.label, end_b.label))
            for st in s.body:
                _lower_stmt(ctx, st)
            ctx.loop_stack.pop()
            ctx.emit(IRInstr("br", None, [cont_b.label]))

            ctx.switch_to(cont_b)
            inc_idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", inc_idx_v, [idx_ptr]))
            one = ctx.tmp(I64)
            ctx.emit(IRInstr("const", one, [1]))
            next_idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("iadd", next_idx_v, [inc_idx_v, one]))
            ctx.emit(IRInstr("store", None, [next_idx_v, idx_ptr]))
            ctx.emit(IRInstr("br", None, [head_b.label]))

            if natural_b is not None:
                ctx.switch_to(natural_b)
                for st in s.orelse:
                    _lower_stmt(ctx, st)
                ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(end_b)
            return
        iter_t = A.expr_type(s.iter)
        if iter_t not in ("list", "dict", "str", "any"):
            raise LowerError(f"unsupported stmt For (iterating {iter_t!r})")
        if iter_t == "dict":
            el_ty = "str"
        elif iter_t == "str":
            el_ty = "str"
        elif iter_t == "any":
            el_ty = "any"
        elif isinstance(s.iter, A.ListLit):
            el_ty = s.iter.el_type
        elif isinstance(s.iter, A.Name):
            el_ty = ctx.slot_el_ty.get(
                s.iter.name,
                ctx.mctx.global_list_el_ty.get(s.iter.name, "int"),
            )
        else:
            el_ty = getattr(s.iter, "list_el_type", "int") or "int"
        if el_ty == "float":
            raise LowerError("unsupported stmt For (float list elements)")
        var_ty = PTR if s.targets else ir_type_for(el_ty)

        if iter_t == "dict":
            dict_v = _lower_expr(ctx, s.iter)
            list_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", list_v, ["_abi_dict_keys", dict_v]))
        else:
            list_v = _lower_expr(ctx, s.iter)
        list_ptr = ctx.ensure_slot(f"__for_iter_{id(s)}", PTR)
        ctx.emit(IRInstr("store", None, [list_v, list_ptr]))
        idx_ptr = ctx.ensure_slot(f"__for_idx_{id(s)}", I64)
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

        head_b = ctx.new_block("forlisthead")
        body_b = ctx.new_block("forlistbody")
        cont_b = ctx.new_block("forlistcont")
        # break-must-skip-else design, see A.While above.
        natural_b = ctx.new_block("forlistnatural") if s.orelse else None
        end_b = ctx.new_block("forlistend")

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cur_list_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_list_v, [list_ptr]))
        len_v = ctx.tmp(I64)
        if iter_t == "str":
            ctx.emit(IRInstr("call", len_v, ["strlen", cur_list_v]))
        else:
            len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", len_addr, [cur_list_v, _LIST_LEN_OFF]))
            ctx.emit(IRInstr("load", len_v, [len_addr]))
        cond = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond, [idx_v, len_v]))
        false_target = natural_b.label if natural_b is not None else end_b.label
        ctx.emit(IRInstr("br.t", None, [cond, body_b.label, false_target]))

        ctx.switch_to(body_b)
        # Reload the buffer pointer fresh each iteration (matching
        # codegen.py's _gen_for_list): an in-body append/extend call may
        # have reallocated it since the head block's check.
        body_list_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", body_list_v, [list_ptr]))
        body_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", body_idx_v, [idx_ptr]))
        if iter_t == "str":
            elem_v = ctx.tmp(var_ty)
            ctx.emit(IRInstr("call", elem_v, ["_abi_str_char_at", body_list_v, body_idx_v]))
        else:
            addr = _list_elem_addr(ctx, body_list_v, body_idx_v)
            elem_v = ctx.tmp(var_ty)
            ctx.emit(IRInstr("load", elem_v, [addr]))
        if s.targets:
            for i, target in enumerate(s.targets):
                idx = ctx.tmp(I64)
                ctx.emit(IRInstr("const", idx, [i]))
                item_addr = _list_elem_addr(ctx, elem_v, idx)
                target_ty = (
                    s.target_types[i]
                    if i < len(s.target_types) and s.target_types[i]
                    else "any"
                )
                item_v = ctx.tmp(ir_type_for(target_ty))
                ctx.emit(IRInstr("load", item_v, [item_addr]))
                _store_loop_target(ctx, target, item_v, target_ty)
        else:
            _store_loop_target(ctx, s.var, elem_v, el_ty)

        ctx.loop_stack.append((cont_b.label, end_b.label))
        for st in s.body:
            _lower_stmt(ctx, st)
        ctx.loop_stack.pop()
        ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        inc_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", inc_idx_v, [idx_ptr]))
        one = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one, [1]))
        next_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx_v, [inc_idx_v, one]))
        ctx.emit(IRInstr("store", None, [next_idx_v, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        if natural_b is not None:
            ctx.switch_to(natural_b)
            for st in s.orelse:
                _lower_stmt(ctx, st)
            ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        return

    if isinstance(s, A.For):
        if s.targets:
            raise LowerError("unsupported stmt For (tuple-unpack targets)")
        args = s.range_args
        if len(args) == 1:
            start_e, stop_e, step_e = A.IntLit(0), args[0], A.IntLit(1)
        elif len(args) == 2:
            start_e, stop_e, step_e = args[0], args[1], A.IntLit(1)
        else:
            start_e, stop_e, step_e = args[0], args[1], args[2]

        # Same global-vs-local write-site bug class as the generic list-For
        # path (see that branch's _store_loop_target fix): a bare
        # ctx.ensure_slot() here always makes a local stack slot, but a
        # module-scope `for i in range(...):` needs i to write through the
        # module global read-side (_name_ptr / _is_global_name) resolves
        # for every other read of `i` -- otherwise every read outside this
        # loop's own body sees the zero-initialized global instead.
        var_ptr = _name_ptr(ctx, s.var, I64)
        ctx.emit(IRInstr("store", None, [_lower_expr(ctx, start_e), var_ptr]))
        stop_name = f"__for_stop_{id(s)}"
        step_name = f"__for_step_{id(s)}"
        stop_ptr = ctx.ensure_slot(stop_name, I64)
        ctx.emit(IRInstr("store", None, [_lower_expr(ctx, stop_e), stop_ptr]))
        step_ptr = ctx.ensure_slot(step_name, I64)
        ctx.emit(IRInstr("store", None, [_lower_expr(ctx, step_e), step_ptr]))

        head_b = ctx.new_block("forhead")
        pos_b = ctx.new_block("forstepposcheck")
        neg_b = ctx.new_block("forstepnegcheck")
        body_b = ctx.new_block("forbody")
        cont_b = ctx.new_block("forcont")
        # Same break-must-skip-else design as A.While above: the
        # condition-false edges (from both pos_b/neg_b) target natural_b
        # (falls into orelse then end_b), while `break` targets end_b
        # directly, bypassing orelse.
        natural_b = ctx.new_block("fornatural") if s.orelse else None
        end_b = ctx.new_block("forend")
        false_target = natural_b.label if natural_b is not None else end_b.label

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        # Sign of step decided at runtime (it need not be a constant): a
        # positive step continues while var < stop; non-positive continues
        # while var > stop. Mirrors codegen.py's legacy _gen_for exactly.
        step_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", step_v, [step_ptr]))
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        is_pos = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.gt", is_pos, [step_v, zero]))
        ctx.emit(IRInstr("br.t", None, [is_pos, pos_b.label, neg_b.label]))

        var_v = ctx.tmp(I64)
        stop_v = ctx.tmp(I64)
        ctx.switch_to(pos_b)
        ctx.emit(IRInstr("load", var_v, [var_ptr]))
        ctx.emit(IRInstr("load", stop_v, [stop_ptr]))
        cond_pos = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_pos, [var_v, stop_v]))
        ctx.emit(IRInstr("br.t", None, [cond_pos, body_b.label, false_target]))

        var_v2 = ctx.tmp(I64)
        stop_v2 = ctx.tmp(I64)
        ctx.switch_to(neg_b)
        ctx.emit(IRInstr("load", var_v2, [var_ptr]))
        ctx.emit(IRInstr("load", stop_v2, [stop_ptr]))
        cond_neg = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.gt", cond_neg, [var_v2, stop_v2]))
        ctx.emit(IRInstr("br.t", None, [cond_neg, body_b.label, false_target]))

        ctx.switch_to(body_b)
        ctx.loop_stack.append((cont_b.label, end_b.label))
        for st in s.body:
            _lower_stmt(ctx, st)
        ctx.loop_stack.pop()
        ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        cur_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", cur_v, [var_ptr]))
        step_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", step_v2, [step_ptr]))
        next_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_v, [cur_v, step_v2]))
        ctx.emit(IRInstr("store", None, [next_v, var_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        if natural_b is not None:
            ctx.switch_to(natural_b)
            for st in s.orelse:
                _lower_stmt(ctx, st)
            ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        return

    if isinstance(s, A.Break):
        if not ctx.loop_stack:
            raise LowerError("'break' outside loop")
        _, break_label = ctx.loop_stack[-1]
        ctx.emit(IRInstr("br", None, [break_label]))
        return

    if isinstance(s, A.Continue):
        if not ctx.loop_stack:
            raise LowerError("'continue' not properly in loop")
        cont_label, _ = ctx.loop_stack[-1]
        ctx.emit(IRInstr("br", None, [cont_label]))
        return

    if isinstance(s, A.Import) or isinstance(s, A.FromImport):
        # Bindings are already resolved by sema (module-level FFI/class/
        # function tables); nothing to do at the IR level -- there's no
        # notion of a "module object" in this pipeline yet, just direct
        # calls to whatever the imported name resolved to.
        return

    if isinstance(s, A.Raise):
        _lower_raise(ctx, s)
        return

    if isinstance(s, A.Del):
        # `del x` -> zero the slot so a later (illegal, sema should have
        # already rejected it) read can't observe a stale value. `del
        # xs[i]` / `del d[key]` -> _abi_list_del/_abi_dict_pop, discarding
        # whatever they return -- matches codegen.py's _gen_stmt Del.
        tgt = s.target
        if isinstance(tgt, A.Name):
            ty = ctx.mctx.global_types.get(tgt.name, ctx.slot_ty.get(tgt.name, I64))
            ptr = _name_ptr(ctx, tgt.name, ty)
            zero = ctx.tmp(ty)
            ctx.emit(IRInstr("const", zero, [0]))
            ctx.emit(IRInstr("store", None, [zero, ptr]))
            return
        if isinstance(tgt, A.Subscript):
            obj_v = _lower_expr(ctx, tgt.obj)
            if A.expr_type(tgt.obj) == "list":
                idx_v = _lower_expr(ctx, tgt.index)
                ctx.emit(IRInstr("call", None, ["_abi_list_del", obj_v, idx_v]))
            else:
                key_v = _lower_dict_key(ctx, tgt.index)
                ctx.emit(IRInstr("call", None, ["_abi_dict_pop", obj_v, key_v]))
            return
        raise LowerError(f"unsupported stmt Del ({type(tgt).__name__})")

    if isinstance(s, A.Try):
        _lower_try(ctx, s)
        return

    raise LowerError(f"unsupported stmt {type(s).__name__}")


def _load_global(ctx: _FuncCtx, sym: str, ty: IRType) -> IRValue:
    """Load a 64-bit value from a named global variable (external symbol)."""
    addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", addr, [sym]))
    val = ctx.tmp(ty)
    ctx.emit(IRInstr("load", val, [addr]))
    return val


def _store_global(ctx: _FuncCtx, sym: str, val: IRValue) -> None:
    """Store a value to a named global variable (external symbol)."""
    addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", addr, [sym]))
    ctx.emit(IRInstr("store", None, [val, addr]))


def _lower_raise(ctx: _FuncCtx, s: A.Raise) -> None:
    """Lower `raise expr` or bare `raise` to a call to _abi_raise(msg, type_id)."""
    if s.value is None:
        # Bare re-raise: forward the currently active exception unchanged.
        # _runtime_exc_msg is NULL until the first raise ever happens, so
        # a bare `raise` with nothing active must report the same error
        # CPython does (RuntimeError: No active exception to reraise)
        # instead of blindly forwarding a NULL message/stale type id --
        # mirrors codegen.py's _gen_stmt Raise handling exactly.
        msg_v = _load_global(ctx, "_runtime_exc_msg", PTR)
        zero = ctx.tmp(PTR)
        ctx.emit(IRInstr("const", zero, [0]))
        has_exc = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.ne", has_exc, [msg_v, zero]))

        has_b = ctx.new_block("reraisehas")
        none_b = ctx.new_block("reraisenone")
        after_b = ctx.new_block("reraiseafter")
        msg_ptr = ctx.ensure_slot(f"__reraise_msg_{id(s)}", PTR)
        type_ptr = ctx.ensure_slot(f"__reraise_type_{id(s)}", I64)
        ctx.emit(IRInstr("br.t", None, [has_exc, has_b.label, none_b.label]))

        ctx.switch_to(has_b)
        type_v = _load_global(ctx, "_runtime_exc_type", I64)
        ctx.emit(IRInstr("store", None, [msg_v, msg_ptr]))
        ctx.emit(IRInstr("store", None, [type_v, type_ptr]))
        ctx.emit(IRInstr("br", None, [after_b.label]))

        ctx.switch_to(none_b)
        no_exc_name = ctx.mctx.intern_str("No active exception to reraise")
        no_exc_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", no_exc_v, [no_exc_name]))
        runtime_err_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", runtime_err_v, [BUILTIN_EXC_IDS["RuntimeError"]]))
        ctx.emit(IRInstr("store", None, [no_exc_v, msg_ptr]))
        ctx.emit(IRInstr("store", None, [runtime_err_v, type_ptr]))
        ctx.emit(IRInstr("br", None, [after_b.label]))

        ctx.switch_to(after_b)
        final_msg = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", final_msg, [msg_ptr]))
        final_type = ctx.tmp(I64)
        ctx.emit(IRInstr("load", final_type, [type_ptr]))
        ctx.emit(IRInstr("call", None, ["_abi_raise", final_msg, final_type]))
    else:
        exc_id = _exc_raise_type_id_ir(s.value)
        exc_id_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", exc_id_v, [exc_id]))
        # Extract or synthesize the message string.
        if isinstance(s.value, A.Call) and s.value.args and A.expr_type(s.value.args[0]) == "str":
            msg_v = _lower_expr(ctx, s.value.args[0])
        elif A.expr_type(s.value) == "str":
            msg_v = _lower_expr(ctx, s.value)
        else:
            empty = ctx.mctx.intern_str("")
            msg_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", msg_v, [empty]))
        ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_id_v]))


def _lower_try(ctx: _FuncCtx, s: A.Try) -> None:
    """Lower try/except/finally via setjmp/longjmp through the _abi_* shim layer.

    Follows the same pattern as codegen.py's _gen_try: install a jmp_buf,
    call _abi_setjmp, branch on the result (0 = normal, nonzero = exception),
    match handlers in order, re-raise if none match, run finally on every path.
    """
    uid = id(s)
    fin_body = s.finally_body or []
    body = list(s.body) + list(s.else_body or [])

    handlers: list = []
    if s.handler:
        handlers.append((s.handler_types, s.bind_name, s.handler))
    handlers.extend(s.extra_handlers)

    # Reserve a raw 64-byte jmp_buf on the stack.
    buf_ptr = ctx.raw_slot(f"__try_buf_{uid}", _JMP_BUF_SIZE)

    # Slots for saved state (parent handler, active exception before this try).
    parent_ptr = ctx.ensure_slot(f"__try_parent_{uid}", PTR)
    prev_msg_ptr = ctx.ensure_slot(f"__try_prev_msg_{uid}", PTR)
    prev_type_ptr = ctx.ensure_slot(f"__try_prev_type_{uid}", I64)

    # --- save current exception state & install our handler ---
    cur_msg = _load_global(ctx, "_runtime_exc_msg", PTR)
    ctx.emit(IRInstr("store", None, [cur_msg, prev_msg_ptr]))
    cur_type = _load_global(ctx, "_runtime_exc_type", I64)
    ctx.emit(IRInstr("store", None, [cur_type, prev_type_ptr]))
    cur_top = _load_global(ctx, "_runtime_handler_top", PTR)
    ctx.emit(IRInstr("store", None, [cur_top, parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", buf_ptr)

    # setjmp(jmp_buf) -> 0 on direct call, nonzero after longjmp
    setjmp_block_index = ctx.blocks.index(ctx.cur)
    setjmp_result = ctx.tmp(I64)
    ctx.emit(IRInstr("call", setjmp_result, ["_abi_setjmp", buf_ptr]))

    handler_b = ctx.new_block(f"try_handler_{uid}")
    body_b = ctx.new_block(f"try_body_{uid}")
    end_b = ctx.new_block(f"try_end_{uid}")

    ctx.emit(IRInstr("br.t", None, [setjmp_result, handler_b.label, body_b.label]))

    # --- try body (+ else) ---
    ctx.switch_to(body_b)
    ctx.try_handler_stack.append(f"__try_parent_{uid}")
    for st in body:
        _lower_stmt(ctx, st)
    ctx.try_handler_stack.pop()
    if not ctx.terminated:
        # Normal completion: restore parent handler, run finally, jump to end.
        parent_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", parent_v, [parent_ptr]))
        _store_global(ctx, "_runtime_handler_top", parent_v)
        for fs in fin_body:
            _lower_stmt(ctx, fs)
        ctx.emit(IRInstr("br", None, [end_b.label]))

    # --- exception path ---
    ctx.switch_to(handler_b)
    # Restore parent handler first (so a raise from inside a handler propagates up).
    parent_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", parent_v2, [parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", parent_v2)

    # Build check blocks: one per handler + one "no match" block.
    n = len(handlers)
    check_blocks = [ctx.new_block(f"try_check_{uid}_{i}") for i in range(n + 1)]
    ctx.emit(IRInstr("br", None, [check_blocks[0].label]))

    for hi in range(n):
        types, bind_name, hbody = handlers[hi]
        ctx.switch_to(check_blocks[hi])
        if types:
            # Type-filtered handler: compare _runtime_exc_type against matching ids.
            matched_b = ctx.new_block(f"try_run_{uid}_{hi}")
            exc_type_v = _load_global(ctx, "_runtime_exc_type", I64)
            for mid in sorted(_exc_matching_ids_ir(types)):
                mid_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", mid_v, [mid]))
                eq = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", eq, [exc_type_v, mid_v]))
                nxt = ctx.new_block(f"try_check_next_{uid}_{hi}_{mid}")
                ctx.emit(IRInstr("br.t", None, [eq, matched_b.label, nxt.label]))
                ctx.switch_to(nxt)
            # None matched -> fall through to next handler
            ctx.emit(IRInstr("br", None, [check_blocks[hi + 1].label]))
            ctx.switch_to(matched_b)
        # Handler matched: optionally bind exception message to a name.
        # _collect_module_globals registers a module-scope `except X as e:`
        # bind_name as a global, so the write here must go through the
        # same global-vs-local check every other read of that name uses
        # (_name_ptr) -- a bare ctx.ensure_slot() always makes a local
        # slot, which every module-scope read then misses entirely,
        # reading the zero-initialized global instead (same bug class as
        # the for-loop variable fixes elsewhere in this file).
        if bind_name is not None:
            bind_ptr = _name_ptr(ctx, bind_name, PTR)
            exc_msg_v = _load_global(ctx, "_runtime_exc_msg", PTR)
            ctx.emit(IRInstr("store", None, [exc_msg_v, bind_ptr]))
        for st in hbody:
            _lower_stmt(ctx, st)
        if not ctx.terminated:
            # Restore active exception state to what it was before this try.
            prev_msg_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", prev_msg_v, [prev_msg_ptr]))
            _store_global(ctx, "_runtime_exc_msg", prev_msg_v)
            prev_type_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", prev_type_v, [prev_type_ptr]))
            _store_global(ctx, "_runtime_exc_type", prev_type_v)
            for fs in fin_body:
                _lower_stmt(ctx, fs)
            ctx.emit(IRInstr("br", None, [end_b.label]))

    # No handler matched (or no except clauses at all — bare try/finally).
    ctx.switch_to(check_blocks[n])
    for fs in fin_body:
        _lower_stmt(ctx, fs)
    # Re-raise the unhandled exception.
    reraise_msg = _load_global(ctx, "_runtime_exc_msg", PTR)
    reraise_type = _load_global(ctx, "_runtime_exc_type", I64)
    ctx.emit(IRInstr("call", None, ["_abi_raise", reraise_msg, reraise_type]))
    # _abi_raise never returns; br to end_b satisfies the IR terminator requirement.
    ctx.emit(IRInstr("br", None, [end_b.label]))

    # Record the full [setjmp block, last handler-related block] span.
    # NOTE: end_b's own index is NOT the right upper bound -- it's
    # created early (right after handler_b/body_b, before the per-handler
    # check_blocks), so blocks created later (the check_blocks handling
    # each `except` clause, which is where a bound exception name or any
    # value read inside the handler body actually lives) sit at HIGHER
    # indices than end_b itself. The true upper bound is simply the last
    # block index this whole _lower_try call has produced so far -- every
    # block it creates, in creation order, is part of this try
    # (including nested try/except inside a handler/finally body, which
    # is fine: a nested try's own narrower region is recorded separately
    # and both cover the value correctly). See IRFunc.try_regions'
    # docstring for why the x86-64 backend's regalloc.py needs this.
    ctx.try_regions.append((setjmp_block_index, len(ctx.blocks) - 1))

    ctx.switch_to(end_b)


def lower_func(
    f: A.FuncDef,
    mctx: _ModuleCtx,
    *,
    visibility: str | None = None,
    module_body: bool = False,
) -> IRFunc:
    declared_globals: set[str] = set()
    _collect_declared_globals(f.body, declared_globals)
    local_names: set[str] = set()
    if not module_body:
        # Seed with the function's own PARAMETERS before scanning the body
        # -- _collect_bound_names only walks statements (assign targets,
        # for-loop vars, etc.), never f.params, so a parameter that the
        # body only ever READS (never reassigns) was previously absent
        # from local_names entirely. When that parameter's name happens to
        # collide with an unrelated module-level global declared elsewhere
        # in the same file (e.g. `def split(lo, hi): ... ` alongside a
        # later top-level `lo, hi = minmax(...)`), _is_global_name's
        # non-module-scope fallback (`return name in
        # ctx.mctx.global_names`) misrouted every read of the parameter to
        # `global_addr` the unrelated global instead of the parameter's
        # own stack slot -- silently reading/dividing by whatever
        # (possibly zero/uninitialized) value that global happened to
        # hold. Confirmed via IR dump + gdb: SIGFPE from `idiv` with a
        # divisor of 0, traced back to `split`'s `lo`/`hi` params being
        # lowered as `global_addr ['lo']`/`['hi']` instead of local slots.
        local_names.update(f.params)
        _collect_bound_names(f.body, local_names)
        local_names.difference_update(declared_globals)
    ctx = _FuncCtx(
        mctx,
        local_names=local_names,
        declared_globals=declared_globals,
        module_body=module_body,
    )
    entry = ctx.new_block("entry")
    ctx.switch_to(entry)
    ctx.shared_zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", ctx.shared_zero, [0]))

    params: list[IRValue] = []
    for i, pname in enumerate(f.params):
        annot = f.param_types[i] if i < len(f.param_types) else None
        ty = ir_type_for(annot[0]) if isinstance(annot, tuple) else I64
        pv = IRValue(f"%arg_{pname}", ty)
        params.append(pv)
        ptr = ctx.ensure_slot(pname, ty)
        ctx.emit(IRInstr("store", None, [pv, ptr]))

    for st in f.body:
        _lower_stmt(ctx, st)

    if not ctx.terminated:
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        ctx.emit(IRInstr("ret", None, [zero]))

    return IRFunc(
        name=f.name,
        params=params,
        ret_type=I64,
        blocks=ctx.blocks,
        visibility=visibility,
        try_regions=ctx.try_regions,
    )


def _resolve_method_owner_in_sigs(classes_sig: dict, class_name: str, method: str) -> str | None:
    seen: set[str] = set()
    cur = class_name
    while cur is not None and cur not in seen:
        seen.add(cur)
        sig = classes_sig.get(cur)
        if sig is None:
            return None
        if method in sig.methods:
            return cur
        cur = sig.parent
    return None


def _reachable_callables(mod: A.Module) -> tuple[list[A.FuncDef], list[A.FuncDef]]:
    method_defs: dict[tuple[str, str], A.FuncDef] = {}
    class_names = {c.name for c in mod.classes}
    func_defs = {f.name: f for f in mod.funcs}
    for cls in mod.classes:
        for m in cls.methods:
            method_defs[(cls.name, m.name)] = m

    classes_sig = getattr(mod, "classes_sig", {})
    needed: set[tuple[str, str]] = set()
    needed_funcs: set[str] = set()
    method_queue: list[tuple[str, str]] = []
    func_queue: list[str] = []

    def add(owner: str | None, method: str | None) -> None:
        if owner is None or method is None:
            return
        key = (owner, method)
        if key in method_defs and key not in needed:
            needed.add(key)
            method_queue.append(key)

    def add_func(name: str | None) -> None:
        if name is None:
            return
        if name in func_defs and name not in needed_funcs:
            needed_funcs.add(name)
            func_queue.append(name)

    def add_resolved(class_name: str, method: str) -> None:
        add(_resolve_method_owner_in_sigs(classes_sig, class_name, method), method)

    def visit(node) -> None:
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, A.MethodCall):
            obj_ty = A.expr_type(node.obj)
            if obj_ty.startswith("instance:"):
                add_resolved(obj_ty.split(":", 1)[1], node.method)
            elif obj_ty == "type" and isinstance(node.obj, A.Name):
                add_resolved(node.obj.name, node.method)
        elif isinstance(node, A.Name):
            add_func(node.name)
        elif isinstance(node, A.Call):
            add_func(node.func)
            if node.func in class_names:
                add_resolved(node.func, "__init__")
            owner = getattr(node, "dunder_call_owner", None)
            if owner is not None:
                add(owner, "__call__")
        elif isinstance(node, A.Lambda):
            add_func(getattr(node, "func_name", None))
        elif isinstance(node, A.BinOp):
            owner = getattr(node, "dunder_owner", None)
            if owner is not None:
                add(owner, getattr(node, "dunder_method", None))
        elif isinstance(node, A.UnaryOp):
            owner = getattr(node, "dunder_owner", None)
            if owner is not None:
                add(owner, getattr(node, "dunder_method", None))
        elif isinstance(node, A.Compare):
            owner = getattr(node, "dunder_owner", None)
            if owner is not None:
                add(owner, getattr(node, "dunder_method", None))
            contains_owner = getattr(node, "dunder_contains_owner", None)
            if contains_owner is not None:
                add(contains_owner, "__contains__")
        elif isinstance(node, A.Subscript):
            owner = getattr(node, "_getitem_class", None)
            if owner is not None:
                add_resolved(owner, "__getitem__")
        elif isinstance(node, A.FString):
            for seg in node.segments:
                seg_ty = A.expr_type(seg)
                if seg_ty.startswith("instance:"):
                    cls_name = seg_ty.split(":", 1)[1]
                    owner = (
                        _resolve_method_owner_in_sigs(classes_sig, cls_name, "__str__")
                        or _resolve_method_owner_in_sigs(classes_sig, cls_name, "__repr__")
                    )
                    method = "__str__" if _resolve_method_owner_in_sigs(classes_sig, cls_name, "__str__") is not None else "__repr__"
                    add(owner, method)

        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, tuple):
            for item in node:
                visit(item)
            return
        if is_dataclass(node):
            for f in fields(node):
                if f.name == "pos":
                    continue
                visit(getattr(node, f.name))

    for st in mod.body:
        visit(st)
    if any(f.name == "main" for f in mod.funcs):
        add_func("main")
    if any(f.name == "_threading_bootstrap" for f in mod.funcs):
        add_func("_threading_bootstrap")

    while method_queue or func_queue:
        while method_queue:
            owner, method = method_queue.pop(0)
            f = method_defs.get((owner, method))
            if f is None:
                continue
            for st in f.body:
                visit(st)
        if not func_queue:
            continue
        name = func_queue.pop(0)
        f = func_defs.get(name)
        if f is None:
            continue
        for st in f.body:
            visit(st)

    out_funcs: list[A.FuncDef] = []
    for f in mod.funcs:
        if f.name in needed_funcs:
            out_funcs.append(f)

    out_methods: list[A.FuncDef] = []
    for cls in mod.classes:
        for m in cls.methods:
            if (cls.name, m.name) not in needed:
                continue
            param_types = list(m.param_types)
            # @staticmethod has no implicit self/cls receiver at all -- its
            # params[0] (if any) is a genuine, ordinary user parameter, not
            # the receiver this "any"-stamp is meant to widen. Forcing it
            # to "any" unconditionally clobbered e.g. `add(a, b)`'s first
            # real int param, while the call site (ir_lower's MethodCall
            # lowering, `ClassName.method(...)`) still passes only the
            # user's actual args with no receiver -- an arg-count/type
            # mismatch that crashed at the assembly level.
            if param_types and "staticmethod" not in m.decorators:
                param_types[0] = ("any", None, None, [], None)
            out_methods.append(
                A.FuncDef(
                    name=f"{cls.name}__{m.name}",
                    params=list(m.params),
                    body=list(m.body),
                    pos=m.pos,
                    defaults=list(m.defaults),
                    param_types=param_types,
                    ret_type=m.ret_type,
                    vararg=m.vararg,
                    kwarg=m.kwarg,
                    asm_body=m.asm_body,
                    asm_symbol=f"{cls.name}__{m.name}" if m.asm_body is not None else None,
                )
            )
    return out_funcs, out_methods


def _is_main_guard_test(expr) -> bool:
    if not isinstance(expr, A.Compare):
        return False
    if expr.ops != ["=="] or len(expr.operands) != 2:
        return False
    left, right = expr.operands

    def _name_is_main_name(node) -> bool:
        return isinstance(node, A.Name) and node.name == "__name__"

    def _lit_is_main(node) -> bool:
        return isinstance(node, A.StrLit) and node.value == "__main__"

    return (_name_is_main_name(left) and _lit_is_main(right)) or (
        _lit_is_main(left) and _name_is_main_name(right)
    )


def _is_bare_main_call(st) -> bool:
    """A top-level statement that directly invokes `main()`, outside any
    `if __name__ == "__main__":` guard -- e.g. a bare `main()` line, or
    `raise SystemExit(main())` written unguarded at module level. The
    linker's synthesized entry stub already calls `__asmpy_module_init`
    (whatever this returns) followed unconditionally by `main` whenever an
    explicit `main` function exists (see `has_module_init` in
    elf_linker.py/pe_linker.py), so replaying either of these shapes here
    would call `main()` a second time."""
    call = None
    if isinstance(st, A.ExprStmt) and isinstance(st.expr, A.Call):
        call = st.expr
    elif (
        isinstance(st, A.Raise)
        and isinstance(st.value, A.Call)
        and st.value.func == "SystemExit"
        and len(st.value.args) == 1
        and isinstance(st.value.args[0], A.Call)
    ):
        call = st.value.args[0]
    return call is not None and call.func == "main" and not call.args


def _module_init_stmts(mod: A.Module) -> list:
    """Top-level statements that should run before an explicit native main().

    Files commonly end with `if __name__ == "__main__": raise SystemExit(main())`.
    The built-in x86-64 backend uses the real `main` symbol as the process
    entry when one exists, so replaying that guard at startup would call main()
    twice. Keep every other top-level statement (global initializers/import
    materialization) and drop only the main-guard wrapper itself -- and, for
    the equally valid unguarded style, a bare top-level call to `main()`
    itself (see `_is_bare_main_call`).
    """
    out: list = []
    for st in mod.body:
        if isinstance(st, A.If) and _is_main_guard_test(st.test):
            continue
        if _is_bare_main_call(st):
            continue
        out.append(st)
    return out


def lower_module(mod: A.Module) -> IRModule:
    top_funcs, method_funcs = _reachable_callables(mod)
    func_sigs = getattr(mod, "funcs_sig", {})
    global_types: dict[str, IRType] = {}
    global_list_el_ty: dict[str, str] = {}
    _collect_module_globals(mod.body, global_types, global_list_el_ty)
    mctx = _ModuleCtx(
        frozenset(c.name for c in mod.classes),
        frozenset(f.name for f in top_funcs) | frozenset(f.name for f in method_funcs),
        func_sigs,
        mod.ffi_funcs,
        getattr(mod, "imported_modules", {}),
        getattr(mod, "classes_sig", {}),
        global_types,
        global_list_el_ty,
        mod.classes,
    )
    # Register each class-var global's type into `global_types` (and
    # therefore `global_names`, computed from it in `_ModuleCtx.__init__`)
    # too -- not just `mctx.data` -- so `_is_global_name`/`_name_ptr`
    # correctly resolve the synthesized init-statement writes below (and
    # any `ClassName.attr` read/write elsewhere) as real global accesses
    # rather than falling back to a same-named local slot.
    for label, default_expr in mctx.class_var_defaults:
        global_types[label] = ir_type_for(A.expr_type(default_expr))
    mctx.global_names = frozenset(global_types)
    for name in sorted(global_types):
        mctx.data.append(IRGlobal(name=name, type=global_types[name], value=None))
    funcs = [lower_func(f, mctx) for f in top_funcs]
    funcs.extend(lower_func(f, mctx) for f in method_funcs)
    # Class-level variable globals (`__cv_<Class>__<var>`) are initialized
    # from their default expressions at startup, before any other
    # module-level code runs -- mirrors codegen.py's `_emit_init_class_vars`
    # (runtime init via a real expression eval, matching how a `class C: x
    # = some_call()` default would need to behave, not a pure compile-time
    # constant fold). Prepended to whichever init body runs first, since
    # `ClassName.attr` may be read from the very first module-level
    # statement.
    class_var_init_stmts: list = [
        A.Assign(target=label, value=default_expr, pos=default_expr.pos)
        for label, default_expr in mctx.class_var_defaults
    ]
    has_explicit_main = any(f.name == "main" for f in mod.funcs)
    if has_explicit_main:
        init_body = class_var_init_stmts + _module_init_stmts(mod)
        if init_body:
            init_body_fn = A.FuncDef(
                name="__asmpy_module_init",
                params=[],
                body=init_body,
            )
            funcs.append(lower_func(init_body_fn, mctx, visibility="global", module_body=True))
    else:
        # No explicit main(): preserve the existing script model where the
        # module body itself becomes the process entry function.
        main_body = A.FuncDef(name="main", params=[], body=class_var_init_stmts + list(mod.body))
        funcs.append(lower_func(main_body, mctx, visibility="global", module_body=True))
    return IRModule(funcs=funcs, data=mctx.data)
