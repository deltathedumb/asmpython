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
from .codegen import (
    BUILTIN_EXC_IDS,
    BUILTIN_EXC_PARENTS,
    BUILTIN_TYPE_IDS,
    EXC_ANY,
    NONE_TYPE_ID,
    UNTAGGED_ID,
)

# Magic word stamped at offset 0 of every boxed-scalar cell (`_abi_new_box`
# in abi_shims.asm -- keep the two in sync). No other runtime object has this
# value at word 0 (a list's word-0 is its capacity, a dict/instance's is 8, a
# string's is its length/first bytes), so a single fault-safe load at offset
# 0 identifies a boxed cell with no risk of dereferencing a raw string/list/
# dict as something it isn't. The Python int is the signed reading of the
# unsigned 0xB0BE11EDB0BE11ED the assembly writes.
BOX_MAGIC = 0xB0BE11EDB0BE11ED - (1 << 64)


# Python builtins that this backend does not model as first-class VALUES --
# builtin functions and exception/base classes that have no runtime object
# here. Referenced as a value (`namespace.setdefault("print", print)`,
# `... = Exception`), a bare name like these resolves to no user func, class,
# builtin-type id, or ffi const, and was never given a real global slot, so
# the generic name-read path loads an UNINITIALIZED slot -- a garbage pointer
# that faults when a later `any`-read tries to box-tag it. Such a reference is
# yielded as None instead (the value-position analogue of the graceful call
# stub), harmless unless actually invoked. Builtin *type* names (int/str/list/
# dict/tuple/set/bool/float/bytes/object/type) are NOT here -- those already
# resolve to a real BUILTIN_TYPE_IDS id in the name-read path. `callable`-style
# ones that a program genuinely calls are handled by their own call lowering;
# this set only affects the bare-value read that would otherwise read garbage.
_UNMODELED_BUILTIN_VALUES = frozenset({
    "print", "len", "range", "abs", "min", "max", "sum", "sorted", "enumerate",
    "zip", "map", "filter", "isinstance", "issubclass", "hasattr", "getattr",
    "setattr", "delattr", "callable", "iter", "next", "reversed", "round",
    "pow", "divmod", "all", "any", "repr", "ascii", "format", "chr", "ord",
    "hash", "id", "slice", "property", "classmethod", "staticmethod",
    "bytearray", "vars", "dir", "input", "open", "globals", "locals",
    "Exception", "BaseException", "NameError", "TypeError", "ValueError",
    "RuntimeError", "AttributeError", "KeyError", "IndexError", "StopIteration",
    "StopAsyncIteration", "ZeroDivisionError", "OverflowError", "ArithmeticError",
    "LookupError", "UnboundLocalError", "NotImplementedError", "OSError",
    "IOError", "ImportError", "ModuleNotFoundError", "AssertionError",
    "GeneratorExit", "KeyboardInterrupt", "SystemExit", "FileNotFoundError",
    "PermissionError", "NotImplemented", "Ellipsis",
})


class LowerError(Exception):
    pass


U8 = IRType("u8")
I32 = IRType("i32")

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
        ffi_consts: dict | None = None,
        imported_modules: dict | None = None,
        classes_sig: dict | None = None,
        global_types: dict[str, IRType] | None = None,
        global_list_el_ty: dict[str, str] | None = None,
        classes: list | None = None,
        mlang_code_funcs: dict | None = None,
        imported_funcs: dict | None = None,
    ) -> None:
        self.data: list[IRGlobal] = []
        self.class_names = class_names
        self.func_names = func_names
        self.func_sigs = func_sigs or {}
        self.ffi_funcs = ffi_funcs or {}
        # asmpython.mlang: uid -> {method_name: mlang_support.MlangFuncSig},
        # one entry per Code(...) literal sema.py's _inject_mlang_if_needed
        # found and compiled. Consulted by A.MethodCall's `mlang:` case.
        self.mlang_code_funcs = mlang_code_funcs or {}
        # FFI constants (e.g. `from math import pi`) -- a bare name bound
        # to a compile-time-known scalar value (stdlib.Const), NOT a real
        # runtime global. `_lower_expr`'s A.Name case checks this BEFORE
        # falling back to the generic slot/global lookup (see there for
        # why: without this, the name silently became an uninitialized
        # local defaulting to I64, corrupting anything that read it).
        self.ffi_consts = ffi_consts or {}
        self.imported_modules = imported_modules or {}
        # import_binary()/.imported dynamic-loading (see the "import_binary
        # dynamic DLL loading" section near the end of this file): handle
        # variable name -> list of (func_name, FuncDef) for every top-level
        # `@<handle>.imported` stub decorated for it. Built once in
        # lower_module from the whole-program mod.funcs, mirroring
        # codegen.py's Codegen.imported_funcs exactly (same dict shape, same
        # ".imported" decorator-suffix scan) so both backends resolve the
        # same set of dynamically-imported functions per handle.
        self.imported_funcs: dict[str, list[tuple[str, "A.FuncDef"]]] = imported_funcs or {}
        # Filled by lower_module once the complete FuncDef list is available.
        # Lifted nested functions need their captured values prepended at every
        # direct call site, including self-recursive calls.
        self.lifted_free_vars: dict[str, list[str]] = {}
        self.lifted_nonlocal_vars: dict[str, set[str]] = {}
        self.classes_sig = classes_sig or {}
        self.global_types = global_types or {}
        self.global_names = frozenset(self.global_types)
        self.global_list_el_ty = global_list_el_ty or {}
        self.class_ids: dict[str, int] = {
            name: i for i, name in enumerate(sorted(class_names))
        }
        # Mutable runtime namespaces for attributes assigned through class
        # objects. Declared class-body defaults retain dedicated globals.
        self.class_object_labels: dict[str, str] = {
            name: f"__classobj_{name}" for name in class_names
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
        self.closure_names: set[str] = set()
        self.closure_free_counts: dict[str, int] = {}
        # Variables holding an ESCAPING closure value -- a closure OBJECT
        # (magic/fn-ptr/captures list) returned from a factory and stored here
        # (`add5 = make_adder(5)`), as opposed to `closure_names` which are a
        # function's OWN locally-bound closures with a statically-known capture
        # count. An escaping closure's capture count isn't known at this call
        # site (the object came from elsewhere), so its call reads the count
        # from the object at runtime. Sema types the factory's result "closure".
        self.closure_value_names: set[str] = set()
        self.nonlocal_names: set[str] = set()
        self.boxed_names: set[str] = set()
        # var name -> concrete asmpython type string, active only while
        # lowering the "then" body of an `if type(x) is T:` / `if
        # isinstance(x, T):` check on that name (pushed/popped by A.If's
        # lowering; see `_narrowed_if_type`). A boxed "any"-typed value
        # (see `_lower_box_any`) can only be safely unboxed ONCE -- static
        # sema typing has no notion of "this specific read happens after a
        # runtime check proved the concrete type," so without this, every
        # read of a checked-any variable either never unboxes (leaving the
        # payload unreachable) or unboxes repeatedly (crashing on the
        # second read, since the first read's raw payload isn't a valid
        # boxed-cell pointer -- see `_lower_expr`'s docstring for the
        # confirmed repro). `_lower_narrowed_name_ptr` consults this to
        # unbox exactly once per branch, into a dedicated scratch slot, so
        # every read inside the narrowed branch after the first reuses the
        # SAME already-unboxed value instead of re-unboxing.
        self.narrowed_types: dict[str, str] = {}
        # var name -> IRValue holding the already-unboxed scratch pointer
        # for the CURRENT narrowed branch (see narrowed_types above).
        # Cleared alongside narrowed_types when the branch is left, so a
        # later, unrelated narrowed branch on the same name starts fresh.
        self.narrowed_cache: dict[str, IRValue] = {}
        self.local_names = local_names or set()
        self.declared_globals = declared_globals or set()
        self.module_body = module_body
        # For a method body, the owning class name and the name of its receiver
        # parameter (`self`/`cls`), so a `<receiver>.<classvar>` access can bind
        # statically to the owner's `__cv_<Owner>__<var>` global. Critical for a
        # @classmethod, whose `cls` is a null placeholder that must never be
        # dereferenced (asmpython has no runtime class objects) -- see
        # `_lower_expr`'s A.Attr handling. Both None for ordinary functions.
        self.method_owner_class: str | None = None
        self.receiver_param: str | None = None
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
        # (setjmp_block_label, member_block_labels) per try/except this
        # function lowers -- populated by the three setjmp-installing lowerings
        # via `_record_try_region`, consumed by lower_func to stamp
        # IRFunc.try_regions for the register allocators. See that field's
        # docstring for why it is a label set rather than an index span.
        #
        # Record the setjmp LABEL where the block is still `ctx.cur`; by the
        # time the region is closed `ctx.cur` has moved on.
        self.try_regions: list[tuple[str, tuple[str, ...]]] = []
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
        # This function's declared return type (asmpython-level string,
        # e.g. "float"/"int"/"instance:X"), set by lower_func right after
        # construction. None at module scope (no return concept there).
        # Used by A.Return's lowering to promote an int return value to
        # float when the function is declared -> float but the returned
        # expression is int-typed (e.g. `return some_int_list[i]` in a
        # function annotated `-> float`) -- without this, the raw int
        # bits got interpreted as a float's bit pattern with no sitofp,
        # producing tiny garbage values like 6.95186e-310.
        self.ret_ty: str | None = None
        # True only when this function's OWN `return` statements provably
        # disagree on concrete type (see `_has_genuinely_heterogeneous_
        # returns`, set once by `lower_func` right after `ret_ty`). Guards
        # the `A.Return` boxing branch (see its own comment) against
        # `ret_ty == "any"` for reasons that have NOTHING to do with
        # genuine heterogeneity -- confirmed via two separate, unrelated
        # real cases: a self-recursive function (`fib(n)`: "any" purely
        # because its own return type isn't resolved yet during sema's
        # fixed-point pass) and a function with unannotated parameters
        # and no call-site type signal (`add6(a, b, c, d, e, f): return a
        # + b + ... + f`, from asmpython's own stack-args test: every
        # param defaults to "any" with no annotation, so the whole sum
        # stays "any" even though nothing about it is heterogeneous).
        # Boxing either of those, with no corresponding unboxing at their
        # callers (this codebase has no safe way to unbox on every read --
        # see `_lower_expr`'s docstring for why that crashed), turned a
        # previously-correct raw int into an unread boxed cell pointer
        # printed as garbage. Restricting boxing to PROVABLE literal
        # heterogeneity is deliberately conservative: it misses a real
        # case too (portapy's `VirtualMachine.run`/`_run_frame`, whose
        # returns are all opaque forwards with no literal type of their
        # own to compare) -- but an under-approximation that leaves a
        # target case unboxed is far safer than an over-approximation
        # that silently corrupts otherwise-correct, currently-passing
        # code across the test suite.
        self.box_any_returns = False

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

    def emit_alloca(self, instr: IRInstr) -> None:
        """Emit a frame reservation into the ENTRY block, wherever we are now.

        An ``alloca`` is a frame offset, not a computed value: it must be
        available on every path, and it must not be conditional on the control
        flow that happened to be current when the slot was first needed.

        Emitting it into the current block breaks both. The slot's defining
        instruction then sits in whichever block first mentioned the name, and a
        block reached another way reads a slot whose ``alloca`` never executed --
        a use its definition does not dominate. It appears to work only because
        the register allocator walks blocks in list order, so a definition at a
        lower index satisfies a use it does not actually dominate; any pass that
        reorders or merges blocks turns those reads into wild pointers.

        The concrete case: lowering duplicates a ``finally`` body once per exit
        path, and the exception-path copy reads the slots the normal-path copy
        allocated. Measured at 36 of 394 corpus cases, 178 violations, also
        covering ``enumerate`` loops and ``match``/``case``.

        Entry-block allocas are what every SSA compiler does, and for this
        reason. Note this deliberately bypasses ``emit``'s dropping of
        instructions after a terminator: reserving stack space is not
        unreachable code, even when the block that first needed it is.
        """
        if not self.blocks:
            self.emit(instr)
            return
        entry = self.blocks[0]
        at = len(entry.instrs)
        if at and entry.instrs[-1].op in ("ret", "br", "br.t"):
            at -= 1                      # stay before the terminator
        entry.instrs.insert(at, instr)

    def ensure_slot(self, name: str, ty: IRType) -> IRValue:
        if name not in self.slot:
            ptr = self.tmp(IRType("ptr"))
            self.slot[name] = ptr
            self.slot_ty[name] = ty
            self.emit_alloca(IRInstr("alloca", ptr, []))
        return self.slot[name]

    def raw_slot(self, name: str, n_bytes: int) -> IRValue:
        """Reserve n_bytes of raw stack space (e.g. a jmp_buf), returning a PTR."""
        if name not in self.slot:
            ptr = self.tmp(PTR)
            self.slot[name] = ptr
            self.slot_ty[name] = PTR
            self.emit_alloca(IRInstr("alloca", ptr, [n_bytes]))
        return self.slot[name]


_BINOP = {
    "+": "iadd", "-": "isub", "*": "imul",
    "//": "idiv", "%": "irem",
    "&": "iand", "|": "ior", "^": "ixor",
    "<<": "shl", ">>": "shr",
}

# AugAssign operator -> (in-place dunder, plain dunder) for instance-typed
# operands. Mirrors sema.py's DUNDER_BINOP (forward-op half only -- an
# augmented assignment's RHS is never the reflected side) plus the
# in-place variant CPython checks first (`__iadd__`/`__isub__`/etc.).
_AUGASSIGN_DUNDER = {
    "+": ("__iadd__", "__add__"),
    "-": ("__isub__", "__sub__"),
    "*": ("__imul__", "__mul__"),
    "/": ("__itruediv__", "__truediv__"),
    "//": ("__ifloordiv__", "__floordiv__"),
    "%": ("__imod__", "__mod__"),
    "**": ("__ipow__", "__pow__"),
    "&": ("__iand__", "__and__"),
    "|": ("__ior__", "__or__"),
    "^": ("__ixor__", "__xor__"),
    "<<": ("__ilshift__", "__lshift__"),
    ">>": ("__irshift__", "__rshift__"),
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


def _load_list_elem(ctx: _FuncCtx, addr: IRValue, el_ty: str) -> IRValue:
    """Load one element of a list/tuple buffer from `addr` (an address from
    `_list_elem_addr`) in the representation its element kind implies.

    The one that is easy to get wrong is "any": such a list stores its scalar
    elements BOXED (the store choke `_lower_value_into_any_slot`), so a raw
    `load` into an I64 yields the box's ADDRESS, not the value. Every consumer
    that then treats it as a number -- `sum()`, a `for` body's `+=`, `min()`,
    `join()` -- silently computes with pointers. Loading PTR-typed and routing
    through `_lower_unbox_any` is what `_lower_expr`'s read choke does for the
    same value reached via a subscript; this is that choke for the open-coded
    buffer walks, which never go through `_lower_expr` at all.

    Unboxing is a safe no-op on a never-boxed element (a raw int, a list/dict/
    instance pointer): the magic-sentinel discriminator in
    `_lower_read_any_tag` never dereferences a non-box.
    """
    if el_ty == "float":
        v = ctx.tmp(F64)
        ctx.emit(IRInstr("load", v, [addr]))
        return v
    if el_ty == "any":
        raw = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", raw, [addr]))
        return _lower_unbox_any(ctx, raw)
    v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", v, [addr]))
    return v


_INPARAM_OUTPARAM_ELEM_SIZE = {
    "int": 8,
    "float": 8,
    "int8": 1,
    "int32": 4,
}


def _inparam_elem_addr(ctx: _FuncCtx, ptr_v: IRValue, idx_v: IRValue, elem_size: int = 8) -> IRValue:
    """Address of ptr_v[idx_v] for an exported function's inparam[T]/
    outparam[T] parameter -- a raw caller-owned C array, unlike
    _list_elem_addr's asmpython-native list: no header/length field to
    skip (ptr_v IS the element buffer's own base address already, not a
    list handle), no Python negative-index wraparound (there is no length
    to wrap against -- the caller's own item_count parameter is the only
    bound, exactly as it is in the equivalent hand-written C glue this
    replaces), and no bounds check (same "caller's responsibility"
    contract as the C ABI it's replacing). `elem_size` is 8 for int/float
    pointees, 1 for int8 (a byte-granularity buffer, e.g. `uint8_t *` --
    see _INPARAM_OUTPARAM_ELEM_SIZE / PortaPy's portapy_dict_key_copy_utf8).
    """
    if elem_size == 1:
        elem_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", elem_addr, [ptr_v, idx_v]))
        return elem_addr
    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [elem_size]))
    byte_off = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", byte_off, [idx_v, eight]))
    elem_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", elem_addr, [ptr_v, byte_off]))
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


def _lower_membership_any(ctx: _FuncCtx, needle_e: A.Expr, hay_e: A.Expr, negate: bool) -> IRValue:
    """`needle in haystack` when the haystack's static type is opaque ("any").

    Emits a runtime tag dispatch mirroring CPython's `in` on the value's
    actual type: a str does a substring test (`_abi_str_index_of`), a
    dict/set does a hashed key/element containment (`_abi_dict_contains`),
    and everything else (list/tuple, or a value this backend can't
    classify) does a linear element scan. The needle is lowered once; each
    branch consumes it in the shape that arm needs (a str compare uses
    `_abi_str_eq`, matching the concrete-type membership paths above). The
    result is a plain 0/1 with `negate` applied uniformly at the end, so the
    caller sees the same boolean shape as every other membership case.
    """
    STR_TAG = BUILTIN_TYPE_IDS["str"]

    needle_v = _lower_expr(ctx, needle_e)
    # Read the haystack as its RAW (possibly still-boxed) cell -- NOT the
    # auto-unboxed value `_lower_expr` would yield. A str stored in an "any"
    # slot is a scalar BOX cell whose tag is only legible on the box itself;
    # unboxing first would strip the tag to a bare str pointer that the tag
    # reader can no longer classify as a str (it would look UNTAGGED and be
    # mis-scanned as a list, faulting). So read the tag from the boxed cell,
    # then unbox per-branch (`_lower_unbox_any` yields the str/scalar payload
    # for a box and is a safe no-op for an already-raw list/dict/set).
    hay_boxed = _lower_expr_inner(ctx, hay_e)
    tag_v = _lower_read_any_tag(ctx, hay_boxed)
    hay_v = _lower_unbox_any(ctx, hay_boxed)
    needle_ty = A.expr_type(needle_e)
    needle_is_str = needle_ty in ("str", "any")

    needle_ptr = ctx.ensure_slot(f"__memany_needle_{id(needle_e)}_{id(hay_e)}", needle_v.type)
    hay_ptr = ctx.ensure_slot(f"__memany_hay_{id(needle_e)}_{id(hay_e)}", PTR)
    res_ptr = ctx.ensure_slot(f"__memany_res_{id(needle_e)}_{id(hay_e)}", I64)
    idx_ptr = ctx.ensure_slot(f"__memany_idx_{id(needle_e)}_{id(hay_e)}", I64)
    ctx.emit(IRInstr("store", None, [needle_v, needle_ptr]))
    ctx.emit(IRInstr("store", None, [hay_v, hay_ptr]))
    zero0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero0, [0]))
    ctx.emit(IRInstr("store", None, [zero0, res_ptr]))

    strcheck_b = ctx.new_block("memanystrcheck")
    str_b = ctx.new_block("memanystr")
    dictcheck_b = ctx.new_block("memanydictcheck")
    dict_b = ctx.new_block("memanydict")
    scan_b = ctx.new_block("memanyscan")
    end_b = ctx.new_block("memanyend")

    ctx.emit(IRInstr("br", None, [strcheck_b.label]))

    # str tag -> substring test
    ctx.switch_to(strcheck_b)
    str_tag_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", str_tag_v, [STR_TAG]))
    is_str = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_str, [tag_v, str_tag_v]))
    ctx.emit(IRInstr("br.t", None, [is_str, str_b.label, dictcheck_b.label]))

    ctx.switch_to(str_b)
    s_needle = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", s_needle, [needle_ptr]))
    s_hay = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", s_hay, [hay_ptr]))
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", idx_v, ["_abi_str_index_of", s_hay, s_needle]))
    neg1 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", neg1, [-1]))
    found_s = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.ne", found_s, [idx_v, neg1]))
    ctx.emit(IRInstr("store", None, [found_s, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    # Not a str: distinguish a dict/set from a list/tuple STRUCTURALLY, since
    # `_lower_read_any_tag` reports UNTAGGED for every raw (never-boxed)
    # container and so can't tell them apart by tag. A dict/set cell
    # (`_abi_new_instance` shape) holds a small TOMBSTONE COUNT at word-2
    # (offset 16); a list/tuple holds its BUFFER POINTER there. So "word-2 is
    # a small int, not a heap address" selects the dict/set hashed-containment
    # path -- the same low-address discriminator `_lower_read_any_tag` uses.
    ctx.switch_to(dictcheck_b)
    PTR_THRESHOLD = 0x10000
    w2_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", w2_addr, [hay_v, 16]))
    w2 = ctx.tmp(I64)
    ctx.emit(IRInstr("load", w2, [w2_addr]))
    thr = ctx.tmp(I64)
    ctx.emit(IRInstr("const", thr, [PTR_THRESHOLD]))
    w2_is_ptr = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.gt", w2_is_ptr, [w2, thr]))
    # w2 looks like a pointer -> list/tuple -> scan; else dict/set -> hashed.
    ctx.emit(IRInstr("br.t", None, [w2_is_ptr, scan_b.label, dict_b.label]))

    ctx.switch_to(dict_b)
    d_hay = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", d_hay, [hay_ptr]))
    key_v = _lower_dict_key(ctx, needle_e)
    contains_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", contains_v, ["_abi_dict_contains", d_hay, key_v]))
    ctx.emit(IRInstr("store", None, [contains_v, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    # otherwise -> linear element scan (list/tuple, or unclassified). Uses
    # the same list header layout the concrete list/tuple case does.
    ctx.switch_to(scan_b)
    scan_idx_ptr = idx_ptr
    zscan = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zscan, [0]))
    ctx.emit(IRInstr("store", None, [zscan, scan_idx_ptr]))
    scan_head = ctx.new_block("memanyscanhead")
    scan_body = ctx.new_block("memanyscanbody")
    scan_found = ctx.new_block("memanyscanfound")
    scan_cont = ctx.new_block("memanyscancont")
    ctx.emit(IRInstr("br", None, [scan_head.label]))

    ctx.switch_to(scan_head)
    cur_i = ctx.tmp(I64)
    ctx.emit(IRInstr("load", cur_i, [scan_idx_ptr]))
    cur_hay = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_hay, [hay_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [cur_hay, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    more = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", more, [cur_i, len_v]))
    ctx.emit(IRInstr("br.t", None, [more, scan_body.label, end_b.label]))

    ctx.switch_to(scan_body)
    b_hay = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", b_hay, [hay_ptr]))
    b_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", b_idx, [scan_idx_ptr]))
    elem_addr = _list_elem_addr(ctx, b_hay, b_idx)
    elem_v = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", elem_v, [elem_addr]))
    b_needle = ctx.tmp(needle_v.type)
    ctx.emit(IRInstr("load", b_needle, [needle_ptr]))
    eq_v = ctx.tmp(I64)
    if needle_is_str:
        ctx.emit(IRInstr("call", eq_v, ["_abi_str_eq", b_needle, elem_v]))
    else:
        ctx.emit(IRInstr("icmp.eq", eq_v, [b_needle, elem_v]))
    ctx.emit(IRInstr("br.t", None, [eq_v, scan_found.label, scan_cont.label]))

    ctx.switch_to(scan_found)
    one_f = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_f, [1]))
    ctx.emit(IRInstr("store", None, [one_f, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(scan_cont)
    ci = ctx.tmp(I64)
    ctx.emit(IRInstr("load", ci, [scan_idx_ptr]))
    one_c = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_c, [1]))
    ni = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", ni, [ci, one_c]))
    ctx.emit(IRInstr("store", None, [ni, scan_idx_ptr]))
    ctx.emit(IRInstr("br", None, [scan_head.label]))

    ctx.switch_to(end_b)
    raw = ctx.tmp(I64)
    ctx.emit(IRInstr("load", raw, [res_ptr]))
    if negate:
        zc = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zc, [0]))
        inv = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", inv, [raw, zc]))
        return inv
    return raw


def _lower_slice_any(ctx: _FuncCtx, e: A.Subscript, sentinel_min: int, sentinel_max: int) -> IRValue:
    """Slice `obj[a:b(:c)]` when obj's static type is opaque ("any").

    Dispatches on the runtime shape -- a str (identified by the scalar-box
    tag read off the still-boxed cell) slices via `_abi_str_slice(_step)`, a
    list/tuple via `_abi_list_slice(_step)` -- exactly as CPython slices by
    type. Each arm applies that type's own missing-bound defaults (str: 0 /
    strlen for no-step, SENTINEL_MIN for step; list: SENTINEL_MIN /
    SENTINEL_MAX). Both return a pointer, carried fine by the "any" result
    type. The index sub-expressions are lowered once, in the shared
    pre-header, so each is emitted exactly once regardless of which arm runs.
    """
    STR_TAG = BUILTIN_TYPE_IDS["str"]
    sl = e.index
    has_step = sl.step is not None

    hay_boxed = _lower_expr_inner(ctx, e.obj)
    tag_v = _lower_read_any_tag(ctx, hay_boxed)
    obj_v = _lower_unbox_any(ctx, hay_boxed)

    # Lower the explicit bounds once (a missing bound is filled per-arm since
    # str and list use different sentinels/defaults).
    start_present = sl.start is not None
    stop_present = sl.stop is not None
    start_expr_v = _lower_expr(ctx, sl.start) if start_present else None
    stop_expr_v = _lower_expr(ctx, sl.stop) if stop_present else None
    step_v = _lower_expr(ctx, sl.step) if has_step else None

    obj_ptr = ctx.ensure_slot(f"__sliceany_obj_{id(e)}", PTR)
    res_ptr = ctx.ensure_slot(f"__sliceany_res_{id(e)}", PTR)
    ctx.emit(IRInstr("store", None, [obj_v, obj_ptr]))

    str_b = ctx.new_block("sliceanystr")
    list_b = ctx.new_block("sliceanylist")
    end_b = ctx.new_block("sliceanyend")

    str_tag_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", str_tag_v, [STR_TAG]))
    is_str = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_str, [tag_v, str_tag_v]))
    ctx.emit(IRInstr("br.t", None, [is_str, str_b.label, list_b.label]))

    # --- str arm ---
    ctx.switch_to(str_b)
    s_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", s_obj, [obj_ptr]))
    if has_step:
        s_start = start_expr_v if start_present else _const_i64(ctx, sentinel_min)
        s_stop = stop_expr_v if stop_present else _const_i64(ctx, sentinel_min)
        sv = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", sv, ["_abi_str_slice_step", s_obj, s_start, s_stop, step_v]))
    else:
        if start_present:
            s_start = start_expr_v
        else:
            s_start = _const_i64(ctx, 0)
        if stop_present:
            s_stop = stop_expr_v
        else:
            s_stop = ctx.tmp(I64)
            ctx.emit(IRInstr("call", s_stop, ["strlen", s_obj]))
        sv = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", sv, ["_abi_str_slice", s_obj, s_start, s_stop]))
    # Re-box the str result so its runtime kind survives into the "any"
    # result slot (a raw str pointer would read UNTAGGED downstream and be
    # mis-formatted as an int). The list/tuple arm stays raw -- containers are
    # never boxed (see `_lower_box_any`), matching the uniform invariant.
    sv_boxed = _lower_box_any(ctx, sv, "str", None)
    ctx.emit(IRInstr("store", None, [sv_boxed, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    # --- list/tuple arm ---
    ctx.switch_to(list_b)
    l_obj = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", l_obj, [obj_ptr]))
    l_start = start_expr_v if start_present else _const_i64(ctx, sentinel_min)
    l_stop = stop_expr_v if stop_present else _const_i64(ctx, sentinel_max)
    if has_step:
        lv = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", lv, ["_abi_list_slice_step", l_obj, l_start, l_stop, step_v]))
    else:
        lv = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", lv, ["_abi_list_slice", l_obj, l_start, l_stop]))
    ctx.emit(IRInstr("store", None, [lv, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", out, [res_ptr]))
    return out


def _const_i64(ctx: _FuncCtx, value: int) -> IRValue:
    v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", v, [value]))
    return v


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
    if hay_ty == "str":
        # `needle in haystack` (substring test) -- `_abi_str_index_of`
        # already exists (used by str.find/str.index) and returns -1
        # when the needle isn't found, exactly the membership test this
        # needs: found iff the returned index != -1. Was previously
        # entirely unimplemented on this backend (a hard LowerError),
        # unlike list/tuple/dict/set membership which all had real
        # lowering already.
        needle_v = _lower_expr(ctx, needle_e)
        hay_v = _lower_expr(ctx, hay_e)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", idx_v, ["_abi_str_index_of", hay_v, needle_v]))
        neg1 = ctx.tmp(I64)
        ctx.emit(IRInstr("const", neg1, [-1]))
        result = ctx.tmp(I64)
        op = "icmp.eq" if negate else "icmp.ne"
        ctx.emit(IRInstr(op, result, [idx_v, neg1]))
        return result
    if hay_ty == "any":
        # `needle in haystack` where the haystack's static type is opaque
        # ("any") -- it may at runtime be a str, a dict/set, or a
        # list/tuple, so dispatch on its runtime tag (the same tag
        # type()/isinstance() read via `_lower_read_any_tag`) exactly as
        # CPython dispatches `in` on the value's actual type. Without this a
        # membership test on any object-typed value (a function parameter, a
        # container element, an `any` return) was a hard LowerError.
        return _lower_membership_any(ctx, needle_e, hay_e, negate)
    if hay_ty not in ("list", "tuple"):
        raise LowerError(
            f"unsupported compare membership ({hay_ty}) at "
            f"{getattr(hay_e, 'pos', None)}: {hay_e!r}"
        )
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
    hay_tuple_elem_tys = A.tuple_element_types(hay_e)
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


def _lower_comprehension_enumerate(ctx: _FuncCtx, e: A.Comprehension) -> IRValue:
    """`[elt for i, x in enumerate(xs)]` -- iterate xs by index, bind the
    running counter to targets[0] and the element to targets[1]. Ports
    codegen.py's `_gen_comprehension_enumerate` IR-op-for-instruction, and
    mirrors this file's own `_lower_dict_comprehension` enumerate branch
    (kept as two separate copies, same as every other list-vs-dict
    comprehension shape in this file, rather than sharing one helper)."""
    inner = e.iter.args[0]  # type: ignore[union-attr]
    src_t = A.expr_type(inner)
    if src_t == "any":
        src_t = "list"
    if src_t not in ("str", "list", "tuple"):
        raise LowerError(f"unsupported expr Comprehension (enumerate {src_t})")
    elem_ty = _iter_element_type(inner)
    iter_v = _lower_expr(ctx, inner)
    iter_ptr = ctx.ensure_slot(f"__comp_enum_iter_{id(e)}", ir_type_for(src_t))
    ctx.emit(IRInstr("store", None, [iter_v, iter_ptr]))

    idx_ptr = ctx.ensure_slot(f"__comp_enum_idx_{id(e)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    if src_t == "str":
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
    out_ptr = ctx.ensure_slot(f"__comp_enum_out_{id(e)}", PTR)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))

    head_b = ctx.new_block("compenumhead")
    body_b = ctx.new_block("compenumbody")
    append_b = ctx.new_block("compenumappend")
    cont_b = ctx.new_block("compenumcont")
    end_b = ctx.new_block("compenumend")

    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    cur_iter_v = ctx.tmp(ir_type_for(src_t))
    ctx.emit(IRInstr("load", cur_iter_v, [iter_ptr]))
    if src_t == "str":
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

    shadow_names = {e.targets[0], e.targets[1]}
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
        if A.expr_type(e.elt) == "float":
            # A list cell is a raw 8-byte int slot; bitcast the double's bits
            # into an I64 so `_abi_list_append` (which copies 8 raw bytes)
            # stores the right pattern instead of leaving the value in an XMM
            # register the helper never reads. Same as list.append(float).
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [item_v]))
            item_v = iv
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


def _lower_comprehension_multi_for(ctx: _FuncCtx, e: A.Comprehension) -> IRValue:
    """`[elt for a in A for b in B ...]` -- multiple `for` clauses, each an
    independent nested loop (not a zip/lockstep walk); the innermost body
    appends `elt` once per combination, same as a nested for-loop would.
    Ports codegen.py's `_gen_comprehension`'s `ef_iters_g` branch (the
    tail of that method, after the single-clause setup) IR-op-for-
    instruction. Was entirely unhandled: `e.extra_for_iters` non-empty
    hit a hard LowerError unconditionally, regardless of shape.

    Block-creation order matters here (see this file's own hard-earned
    lesson on `ctx.new_block`/regalloc liveness): every level's
    head/body block must be created BEFORE any inner level's blocks
    (outer-to-inner, matching real control-flow descent), and every
    level's cont/end block must be created AFTER the innermost level's
    append/skip blocks (inner-to-outer, matching the real unwind -- a
    level's cont/end is only reached once its nested loop has fully
    finished). Creating cont/end blocks upfront in "declaration order"
    instead of this real-traversal order is exactly the shape that
    previously crashed regalloc with a bare KeyError('%tN') elsewhere in
    this file.
    """
    outer_iter_ty = A.expr_type(e.iter)
    if outer_iter_ty == "any":
        outer_iter_ty = "list"
    if outer_iter_ty not in ("str", "list", "tuple"):
        raise LowerError(f"unsupported expr Comprehension (iter {outer_iter_ty})")

    all_iters = [e.iter] + list(e.extra_for_iters)
    all_vars = [e.var] + list(e.extra_for_vars)
    all_targets = [e.targets] + list(e.extra_for_targets)
    all_conds = [e.cond] + list(e.extra_for_conds)
    n_levels = len(all_iters)

    # --- level 0: evaluate iterable, allocate result list (outer clause
    # only -- inner clauses don't grow the result list themselves) ---
    iter_ptrs: list = []
    idx_ptrs: list = []
    level_tys: list = []

    iter_v0 = _lower_expr(ctx, e.iter)
    iter_ptr0 = ctx.ensure_slot(f"__mcomp_iter_0_{id(e)}", ir_type_for(outer_iter_ty))
    ctx.emit(IRInstr("store", None, [iter_v0, iter_ptr0]))
    iter_ptrs.append(iter_ptr0)
    level_tys.append(outer_iter_ty)

    if outer_iter_ty == "str":
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", len_v, ["strlen", iter_v0]))
    else:
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [iter_v0, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
    cap_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", cap_v, [1]))
    real_cap_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", real_cap_v, [len_v, cap_v]))
    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_new_list", real_cap_v]))
    out_ptr = ctx.ensure_slot(f"__mcomp_out_{id(e)}", PTR)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))

    idx_ptr0 = ctx.ensure_slot(f"__mcomp_idx_0_{id(e)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr0]))
    idx_ptrs.append(idx_ptr0)

    # Remaining clauses' iterables are evaluated lazily (inside the
    # enclosing loop body, per real Python semantics -- e.g. `for row in
    # matrix for x in row` re-evaluates `row`'s binding each outer
    # iteration), so only reserve their type/slot info now.
    for k in range(1, n_levels):
        it_ty = A.expr_type(all_iters[k])
        if it_ty == "any":
            it_ty = "list"
        if it_ty not in ("str", "list", "tuple"):
            raise LowerError(f"unsupported expr Comprehension (iter {it_ty})")
        level_tys.append(it_ty)
        iter_ptrs.append(ctx.ensure_slot(f"__mcomp_iter_{k}_{id(e)}", ir_type_for(it_ty)))
        idx_ptrs.append(ctx.ensure_slot(f"__mcomp_idx_{k}_{id(e)}", I64))

    # --- create blocks in real traversal order: for each level (outer to
    # inner), an "init" block (re-binds level k's iterable + resets its
    # index -- skipped for level 0, already done above) followed by its
    # head/body; then append/skip at the innermost; then cont/end
    # inner-to-outer on the unwind. A dedicated init_bs[k] (rather than
    # folding the init into body_bs[k-1] or head_bs[k]) is required
    # because head_bs[k] is ALSO reached from cont_bs[k] (the loop-back
    # edge, which must NOT re-init), and body_bs[k-1] is one block shared
    # by both the cond-pass and cond-fail paths -- an unconditional
    # "next block" for the pass-only re-init needs its own block. ---
    head_bs = []
    body_bs = []
    init_bs: list = [None]  # level 0 has no init block (handled above)
    for k in range(n_levels):
        if k > 0:
            init_bs.append(ctx.new_block(f"mcompinit{k}"))
        head_bs.append(ctx.new_block(f"mcomphead{k}"))
        body_bs.append(ctx.new_block(f"mcompbody{k}"))
    append_b = ctx.new_block("mcompappend")
    cont_bs = []
    end_bs = []
    for k in range(n_levels - 1, -1, -1):
        cont_bs_rev_slot = ctx.new_block(f"mcompcont{k}")
        end_bs_rev_slot = ctx.new_block(f"mcompend{k}")
        cont_bs.append(cont_bs_rev_slot)
        end_bs.append(end_bs_rev_slot)
    cont_bs.reverse()
    end_bs.reverse()
    # cont_bs[k]/end_bs[k] now index by level k, same as head_bs/body_bs.

    shadow_names: set = set()

    def emit_level_head(k: int) -> None:
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptrs[k]]))
        cur_iter_v = ctx.tmp(ir_type_for(level_tys[k]))
        ctx.emit(IRInstr("load", cur_iter_v, [iter_ptrs[k]]))
        if level_tys[k] == "str":
            cur_len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("call", cur_len_v, ["strlen", cur_iter_v]))
        else:
            cur_len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", cur_len_addr, [cur_iter_v, _LIST_LEN_OFF]))
            cur_len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", cur_len_v, [cur_len_addr]))
        keep_going = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, cur_len_v]))
        ctx.emit(IRInstr("br.t", None, [keep_going, body_bs[k].label, end_bs[k].label]))

    def emit_level_body(k: int) -> None:
        body_iter_v = ctx.tmp(ir_type_for(level_tys[k]))
        ctx.emit(IRInstr("load", body_iter_v, [iter_ptrs[k]]))
        body_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", body_idx_v, [idx_ptrs[k]]))
        if level_tys[k] == "str":
            elem_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", elem_v, ["_abi_str_char_at", body_iter_v, body_idx_v]))
            elem_kind = "str"
        else:
            elem_addr = _list_elem_addr(ctx, body_iter_v, body_idx_v)
            src_ty = A.expr_type(all_iters[k])
            elem_kind = "any" if src_ty == "any" else _iter_element_type(all_iters[k])
            elem_v = ctx.tmp(ir_type_for(elem_kind))
            ctx.emit(IRInstr("load", elem_v, [elem_addr]))

        multi_targets = all_targets[k]
        if multi_targets:
            elem_types = getattr(all_iters[k], "tuple_elem_types", [])
            for i, target in enumerate(multi_targets):
                tidx = ctx.tmp(I64)
                ctx.emit(IRInstr("const", tidx, [i]))
                item_addr = _list_elem_addr(ctx, elem_v, tidx)
                target_ty = elem_types[i] if i < len(elem_types) else "any"
                item_v = ctx.tmp(ir_type_for(target_ty))
                ctx.emit(IRInstr("load", item_v, [item_addr]))
                _store_loop_target(ctx, target, item_v, target_ty)
                if isinstance(target, str):
                    shadow_names.add(target)
        else:
            var_name = all_vars[k]
            var_ptr = ctx.ensure_slot(var_name, ir_type_for(elem_kind))
            ctx.emit(IRInstr("store", None, [elem_v, var_ptr]))
            shadow_names.add(var_name)

        pass_target = init_bs[k + 1].label if k + 1 < n_levels else append_b.label
        cond = all_conds[k]
        if cond is not None:
            cond_v = _lower_truthy(ctx, cond)
            ctx.emit(IRInstr("br.t", None, [cond_v, pass_target, cont_bs[k].label]))
        else:
            ctx.emit(IRInstr("br", None, [pass_target]))

    def emit_level_init(k: int) -> None:
        # Level k's "fresh entry" point (once per level-(k-1) element,
        # e.g. once per `row`): (re-)evaluate level k's iterable and reset
        # its index to 0. Not hoistable to before the outer loop -- it may
        # depend on the enclosing level's own loop variable (`row` in
        # `for x in row`) and must be freshly bound on every outer pass,
        # not just the first. Reached only from the enclosing level's
        # cond-PASS path (see pass_target above); the loop-back edge
        # (cont_bs[k] -> head_bs[k]) bypasses this block entirely, so it
        # never re-inits mid-loop.
        it_v = _lower_expr(ctx, all_iters[k])
        ctx.emit(IRInstr("store", None, [it_v, iter_ptrs[k]]))
        zero_k = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_k, [0]))
        ctx.emit(IRInstr("store", None, [zero_k, idx_ptrs[k]]))
        ctx.emit(IRInstr("br", None, [head_bs[k].label]))

    def emit_level_cont(k: int) -> None:
        inc_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", inc_idx_v, [idx_ptrs[k]]))
        one = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one, [1]))
        next_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx_v, [inc_idx_v, one]))
        ctx.emit(IRInstr("store", None, [next_idx_v, idx_ptrs[k]]))
        ctx.emit(IRInstr("br", None, [head_bs[k].label]))

    ctx.emit(IRInstr("br", None, [head_bs[0].label]))
    for k in range(n_levels):
        if k > 0:
            ctx.switch_to(init_bs[k])
            emit_level_init(k)
        ctx.switch_to(head_bs[k])
        emit_level_head(k)

    ctx.comprehension_shadows.append(shadow_names)
    try:
        for k in range(n_levels):
            ctx.switch_to(body_bs[k])
            emit_level_body(k)

        ctx.switch_to(append_b)
        cur_out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_out_v, [out_ptr]))
        item_v = _lower_expr(ctx, e.elt)
        if A.expr_type(e.elt) == "float":
            # A list cell is a raw 8-byte int slot; bitcast the double's bits
            # into an I64 so `_abi_list_append` stores the right pattern.
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [item_v]))
            item_v = iv
        ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out_v, item_v]))
        ctx.emit(IRInstr("br", None, [cont_bs[n_levels - 1].label]))
    finally:
        ctx.comprehension_shadows.pop()

    for k in range(n_levels - 1, -1, -1):
        ctx.switch_to(cont_bs[k])
        emit_level_cont(k)
        ctx.switch_to(end_bs[k])
        if k > 0:
            ctx.emit(IRInstr("br", None, [cont_bs[k - 1].label]))

    ctx.switch_to(end_bs[0])
    final_out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out_v, [out_ptr]))
    return final_out_v


def _record_try_region(ctx, setjmp_block_label: str) -> None:
    """Record the blocks belonging to the try whose setjmp sits in `setjmp_block_label`.

    A region is the SET of blocks this try's lowering created -- every block
    after the setjmp block, at the moment the region is closed. It is recorded
    as labels, and as a set rather than a span, because both properties are
    load-bearing for the optimizer:

      labels, not indices -- inserting or deleting any earlier block renumbers
      positions, silently sliding the region onto unrelated code.

      a set, not a (start, end) pair -- a pair is still positional even when its
      endpoints are labels. Block merging can fuse the end block into an earlier
      one, and the implied span then collapses to a fraction of the try. That
      was observed: `blockmerge` shrank a region from ten blocks to three, the
      allocator stopped extending liveness across the handler, and a `finally`
      that runs on `break` lost its output.

    Membership is what the consumers actually want -- "is this block executing
    while the try is active" -- so recording it directly removes the ordering
    assumption instead of encoding it.
    """
    labels = [b.label for b in ctx.blocks]
    try:
        start = labels.index(setjmp_block_label)
    except ValueError:            # setjmp block gone: nothing to protect
        return
    members = tuple(labels[start + 1:])
    if members:
        ctx.try_regions.append((setjmp_block_label, members))


def _lower_comprehension_instance_iter(ctx: _FuncCtx, e: A.Comprehension, cls_name: str) -> IRValue:
    """`[elt for x in obj]` where obj is a user class with __iter__/
    __next__ (this also covers a yield-based generator function's
    returned object -- see `_lower_comprehension`'s dispatch comment for
    why that's the same shape, not separate machinery). Mirrors
    codegen.py's `_gen_comprehension_instance_iter` and this file's own
    `_lower_for_iter_protocol` (A.For's identical iterator-protocol loop)
    -- same setjmp/StopIteration-catch shape, but appends to a result
    list each iteration instead of running loop-body statements."""
    uid = id(e)
    owner = _resolve_method_owner(ctx, cls_name, "__iter__") or cls_name
    next_owner = _resolve_method_owner(ctx, cls_name, "__next__") or cls_name

    obj_v = _lower_expr(ctx, e.iter)
    iter_ptr = ctx.ensure_slot(f"__comp_iter_obj_{uid}", PTR)
    ctx.emit(IRInstr("store", None, [obj_v, iter_ptr]))
    iter_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", iter_v, [f"{owner}____iter__", obj_v]))
    ctx.emit(IRInstr("store", None, [iter_v, iter_ptr]))

    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_new_list", ctx.shared_zero]))
    out_ptr = ctx.ensure_slot(f"__comp_iter_out_{uid}", PTR)
    ctx.emit(IRInstr("store", None, [out_v, out_ptr]))

    buf_ptr = ctx.raw_slot(f"__comp_iter_buf_{uid}", _JMP_BUF_SIZE)
    parent_ptr = ctx.ensure_slot(f"__comp_iter_parent_{uid}", PTR)
    prev_msg_ptr = ctx.ensure_slot(f"__comp_iter_prev_msg_{uid}", PTR)
    prev_type_ptr = ctx.ensure_slot(f"__comp_iter_prev_type_{uid}", I64)

    top_b = ctx.new_block(f"comp_iter_top_{uid}")
    handler_b = ctx.new_block(f"comp_iter_handler_{uid}")
    body_b = ctx.new_block(f"comp_iter_body_{uid}")
    append_b = ctx.new_block(f"comp_iter_append_{uid}")
    cont_b = ctx.new_block(f"comp_iter_cont_{uid}")
    end_b = ctx.new_block(f"comp_iter_end_{uid}")
    ctx.emit(IRInstr("br", None, [top_b.label]))

    ctx.switch_to(top_b)
    cur_msg = _load_global(ctx, "_runtime_exc_msg", PTR)
    ctx.emit(IRInstr("store", None, [cur_msg, prev_msg_ptr]))
    cur_type = _load_global(ctx, "_runtime_exc_type", I64)
    ctx.emit(IRInstr("store", None, [cur_type, prev_type_ptr]))
    cur_top = _load_global(ctx, "_runtime_handler_top", PTR)
    ctx.emit(IRInstr("store", None, [cur_top, parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", buf_ptr)

    setjmp_block_label = ctx.cur.label
    setjmp_result = ctx.tmp(I64)
    ctx.emit(IRInstr("call", setjmp_result, ["_abi_setjmp", buf_ptr]))
    ctx.emit(IRInstr("br.t", None, [setjmp_result, handler_b.label, body_b.label]))

    ctx.switch_to(body_b)
    # Handler must stay INSTALLED across the __next__ call (that's the
    # call that can raise StopIteration) -- see _lower_for_iter_protocol's
    # identical comment for the confirmed-by-repro reason the restore
    # can't happen before this call.
    cur_iter = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_iter, [iter_ptr]))
    next_sig = ctx.mctx.classes_sig.get(next_owner)
    next_msig = next_sig.methods.get("__next__") if next_sig is not None else None
    el_kind = "any"
    if next_msig is not None and getattr(next_msig, "ret_type", None):
        el_kind = next_msig.ret_type[0]
    next_v = ctx.tmp(ir_type_for(el_kind))
    ctx.emit(IRInstr("call", next_v, [f"{next_owner}____next__", cur_iter]))
    parent_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", parent_v, [parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", parent_v)

    shadow_names: set = set()
    if e.targets:
        elem_types = getattr(e.iter, "tuple_elem_types", [])
        for i, target in enumerate(e.targets):
            idx = ctx.tmp(I64)
            ctx.emit(IRInstr("const", idx, [i]))
            item_addr = _list_elem_addr(ctx, next_v, idx)
            target_ty = elem_types[i] if i < len(elem_types) else "any"
            item_v = ctx.tmp(ir_type_for(target_ty))
            ctx.emit(IRInstr("load", item_v, [item_addr]))
            _store_loop_target(ctx, target, item_v, target_ty)
            if isinstance(target, str):
                shadow_names.add(target)
    else:
        var_ptr = ctx.ensure_slot(e.var, ir_type_for(el_kind))
        ctx.emit(IRInstr("store", None, [next_v, var_ptr]))
        shadow_names.add(e.var)

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
        if A.expr_type(e.elt) == "float":
            # A list cell is a raw 8-byte int slot; bitcast the double's bits
            # into an I64 so `_abi_list_append` (which copies 8 raw bytes)
            # stores the right pattern instead of leaving the value in an XMM
            # register the helper never reads. Same as list.append(float).
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [item_v]))
            item_v = iv
        ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_out_v, item_v]))
        ctx.emit(IRInstr("br", None, [cont_b.label]))
    finally:
        ctx.comprehension_shadows.pop()

    ctx.switch_to(cont_b)
    ctx.emit(IRInstr("br", None, [top_b.label]))

    ctx.switch_to(handler_b)
    parent_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", parent_v2, [parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", parent_v2)
    exc_type_v = _load_global(ctx, "_runtime_exc_type", I64)
    stop_iter_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", stop_iter_v, [BUILTIN_EXC_IDS["StopIteration"]]))
    is_stop = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_stop, [exc_type_v, stop_iter_v]))
    reraise_b = ctx.new_block(f"comp_iter_reraise_{uid}")
    ctx.emit(IRInstr("br.t", None, [is_stop, end_b.label, reraise_b.label]))

    ctx.switch_to(reraise_b)
    reraise_msg = _load_global(ctx, "_runtime_exc_msg", PTR)
    ctx.emit(IRInstr("call", None, ["_abi_raise", reraise_msg, exc_type_v]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    _record_try_region(ctx, setjmp_block_label)
    ctx.switch_to(end_b)
    final_out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", final_out_v, [out_ptr]))
    return final_out_v


def _lower_comprehension(ctx: _FuncCtx, e: A.Comprehension) -> IRValue:
    # `[elt for i, x in enumerate(xs)]` -- mirrors codegen.py's dedicated
    # `_gen_comprehension_enumerate` (and this file's own identical special
    # case already present in `_lower_dict_comprehension`, just below).
    # Was entirely unhandled for LIST comprehensions specifically: without
    # this, `A.expr_type(e.iter)` for a bare `enumerate(...)` call is
    # "int" (sema types the raw builtin-call result leniently, there's no
    # dedicated "enumerate object" type), which isn't one of the
    # str/list/tuple shapes the generic path below accepts, so it raised
    # "unsupported expr Comprehension (iter int)" immediately.
    if (
        isinstance(e.iter, A.Call)
        and e.iter.func == "enumerate"
        and len(e.iter.args) >= 1
        and e.targets
        and len(e.targets) == 2
    ):
        return _lower_comprehension_enumerate(ctx, e)
    if e.extra_for_iters:
        return _lower_comprehension_multi_for(ctx, e)
    outer_ty = A.expr_type(e.iter)
    if outer_ty.startswith("instance:"):
        # `[elt for x in obj]` where obj is a user class with __iter__/
        # __next__ (or, equivalently, a generator-function's returned
        # object -- sema.py desugars `def f(): ... yield v ...` into a
        # `_genobj_f` class with exactly those two methods well before
        # this file ever sees it, so this is the SAME shape, not a
        # separate generator-object machinery). Mirrors codegen.py's
        # `_gen_comprehension_instance_iter` and this file's own
        # `_lower_for_iter_protocol` (A.For's identical iterator-protocol
        # loop): setjmp/StopIteration-catch around each __next__() call.
        # Was entirely unhandled: `A.expr_type(e.iter)` starting with
        # "instance:" isn't one of the str/list/tuple shapes the generic
        # path below accepts, so it raised "unsupported expr Comprehension
        # (iter instance:X)" immediately, for BOTH a real user __iter__/
        # __next__ class and any yield-based generator function's result.
        return _lower_comprehension_instance_iter(ctx, e, outer_ty.split(":", 1)[1])
    iter_ty = A.expr_type(e.iter)
    if iter_ty == "any":
        iter_ty = "list"
    # A dict/set comprehension source iterates its KEYS -- convert to the
    # plain key-list via `_abi_dict_keys` (exactly as A.For's generic
    # iterable path does for `for x in someset`) and iterate that list. Was
    # a hard "unsupported expr Comprehension (iter set)"/(iter dict) error.
    iter_from_keys = iter_ty in ("dict", "set")
    if iter_from_keys:
        iter_ty = "list"
    if iter_ty not in ("str", "list", "tuple"):
        raise LowerError(f"unsupported expr Comprehension (iter {iter_ty})")

    if iter_from_keys:
        src_v = _lower_expr(ctx, e.iter)
        iter_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", iter_v, ["_abi_dict_keys", src_v]))
    else:
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
        if iter_from_keys:
            # `_abi_dict_keys` yields the set/dict keys as str-shaped values
            # -- mirror A.For's generic set/dict iteration, which types the
            # loop var "str" for exactly this reason (see its `el_ty = "str"`
            # for dict/set). A non-str key kind isn't distinguished by the
            # key-list ABI, so "str" is the correct, For-consistent choice.
            elem_kind = "str"
        else:
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
        if A.expr_type(e.elt) == "float":
            # A list cell is a raw 8-byte int slot; bitcast the double's bits
            # into an I64 so `_abi_list_append` (which copies 8 raw bytes)
            # stores the right pattern instead of leaving the value in an XMM
            # register the helper never reads. Same as list.append(float).
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [item_v]))
            item_v = iv
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
        and e.iter.func == "zip"
        and len(e.iter.args) >= 2
        and e.targets
        and len(e.targets) == len(e.iter.args)
    ):
        # `{k: v for k, v in zip(keys, vals)}` -- was entirely unhandled:
        # the fallback path below accepts `iter_ty == "list"` (which is
        # exactly what sema.py types a bare `zip(...)` call as, see
        # sema.py's `e.func == "zip"` handling), so it fell through to
        # `_lower_expr(ctx, e.iter)` on the raw `zip(...)` Call node --
        # there's no generic "bare zip() call" lowering (only a dedicated
        # `list(zip(...))` pattern exists elsewhere in this file), so it
        # tried to link a real function symbol named `zip` and failed at
        # link time ("undefined symbol 'zip' has no known DLL"). Ports
        # `_lower_for_zip`'s N-list lockstep walk (stopping at the
        # shortest input, real zip() truncation semantics) but performs a
        # dict insert per iteration instead of a list append/body lower.
        n = len(e.iter.args)
        zexprs = list(e.iter.args)
        znames = list(e.targets)
        iter_ptrs = [ctx.ensure_slot(f"__dcompzip_it{k}_{id(e)}", PTR) for k in range(n)]
        for k, ze in enumerate(zexprs):
            v = _lower_expr(ctx, ze)
            ctx.emit(IRInstr("store", None, [v, iter_ptrs[k]]))

        stop_ptr = ctx.ensure_slot(f"__dcompzip_stop_{id(e)}", I64)
        first_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", first_v, [iter_ptrs[0]]))
        first_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", first_len_addr, [first_v, _LIST_LEN_OFF]))
        stop_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", stop_v, [first_len_addr]))
        ctx.emit(IRInstr("store", None, [stop_v, stop_ptr]))
        for k in range(1, n):
            cur_stop = ctx.tmp(I64)
            ctx.emit(IRInstr("load", cur_stop, [stop_ptr]))
            it_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", it_v, [iter_ptrs[k]]))
            len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", len_addr, [it_v, _LIST_LEN_OFF]))
            len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", len_v, [len_addr]))
            is_shorter = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.lt", is_shorter, [len_v, cur_stop]))
            min_ptr = ctx.ensure_slot(f"__dcompzip_min_{k}_{id(e)}", I64)
            shorter_b = ctx.new_block(f"dcompzipmin_shorter_{k}")
            keep_b = ctx.new_block(f"dcompzipmin_keep_{k}")
            after_b = ctx.new_block(f"dcompzipmin_after_{k}")
            ctx.emit(IRInstr("br.t", None, [is_shorter, shorter_b.label, keep_b.label]))
            ctx.switch_to(shorter_b)
            ctx.emit(IRInstr("store", None, [len_v, min_ptr]))
            ctx.emit(IRInstr("br", None, [after_b.label]))
            ctx.switch_to(keep_b)
            ctx.emit(IRInstr("store", None, [cur_stop, min_ptr]))
            ctx.emit(IRInstr("br", None, [after_b.label]))
            ctx.switch_to(after_b)
            new_stop = ctx.tmp(I64)
            ctx.emit(IRInstr("load", new_stop, [min_ptr]))
            ctx.emit(IRInstr("store", None, [new_stop, stop_ptr]))

        head_b = ctx.new_block("dcompziphead")
        body_b = ctx.new_block("dcompzipbody")
        insert_b = ctx.new_block("dcompzipinsert")
        cont_b = ctx.new_block("dcompzipcont")
        end_b = ctx.new_block("dcompzipend")

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        i_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", i_v, [idx_ptr]))
        stop_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", stop_v2, [stop_ptr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [i_v, stop_v2]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        i_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", i_v2, [idx_ptr]))
        for k in range(n):
            it_v2 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", it_v2, [iter_ptrs[k]]))
            elem_ty = _iter_element_type(zexprs[k])
            addr = _list_elem_addr(ctx, it_v2, i_v2)
            val = ctx.tmp(F64 if elem_ty == "float" else I64)
            ctx.emit(IRInstr("load", val, [addr]))
            _store_loop_target(ctx, znames[k], val, elem_ty)
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
        if val_v.type is F64:
            # dict cells are raw 8-byte slots -- store a float's bits as I64
            # (same as a plain `d[k] = <float>` assignment does).
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [val_v]))
            val_v = iv
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", cur_out_v, key_v, val_v]))
        ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        inc_i = ctx.tmp(I64)
        ctx.emit(IRInstr("load", inc_i, [idx_ptr]))
        one_i = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_i, [1]))
        next_i = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_i, [inc_i, one_i]))
        ctx.emit(IRInstr("store", None, [next_i, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        final_out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", final_out_v, [out_ptr]))
        return final_out_v

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
        if val_v.type is F64:
            # dict cells are raw 8-byte slots -- store a float's bits as I64
            # (same as a plain `d[k] = <float>` assignment does).
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [val_v]))
            val_v = iv
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
    if val_v.type is F64:
        # dict cells are raw 8-byte slots -- store a float's bits as I64
        # (same as a plain `d[k] = <float>` assignment does).
        iv = ctx.tmp(I64)
        ctx.emit(IRInstr("bitcast_f2i", iv, [val_v]))
        val_v = iv
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
    # `load8` (a direct indexed byte load, base+index in one op) was never
    # implemented in codegen.py's IR-op dispatcher at all -- it silently
    # compiled to a bare `nop` (the dispatcher's unknown-op fallback),
    # leaving `ch` holding whatever stale value already occupied its
    # allocated register, read back as the SAME wrong constant every loop
    # iteration (confirmed via disassembly: the `load8` site compiled to
    # a lone `nop`, and the following `zext` read an untouched register).
    # `gep`+`load` (byte-address arithmetic then an ordinary U8-typed
    # load, exactly what codegen.py's `tname in ("i8","u8")` load case
    # already handles) covers the same shape with ops that actually
    # exist, so this ports to that instead of adding a whole new op.
    char_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", char_addr, [body_str, body_idx]))
    ch = ctx.tmp(U8)
    ctx.emit(IRInstr("load", ch, [char_addr]))
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


def _lower_os_listdir(ctx: _FuncCtx, path_arg) -> IRValue:
    """`os.listdir([path])` -> list[str]. Ports codegen.py's
    `_emit_os_listdir` (Windows target) IR-op-for-instruction: shells
    out to `dir /b [path]` via `_popen`/`fgetc`/`_pclose` (no direct
    Win32 FindFirstFile/FindNextFile binding exists in this codebase's
    curated FFI surface, and this backend has no directory-listing
    runtime helper of its own) rather than a native directory-walk API,
    reading the piped output char-by-char, splitting on `\\n` (skipping
    `\\r`), and appending each non-empty line to a fresh list. Was
    entirely unimplemented on this backend -- every call fell through
    to the generic opaque-receiver stub, which returns a plain 0, later
    crashing when the caller used that as a real list pointer."""
    if path_arg is not None:
        pfx_name = ctx.mctx.intern_str("dir /b ")
        pfx_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", pfx_v, [pfx_name]))
        path_v = _lower_expr(ctx, path_arg)
        cmd_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", cmd_v, ["_abi_str_concat", pfx_v, path_v]))
    else:
        cmd_name = ctx.mctx.intern_str("dir /b")
        cmd_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", cmd_v, [cmd_name]))

    mode_name = ctx.mctx.intern_str("r")
    mode_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", mode_v, [mode_name]))
    pipe_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", pipe_v, ["_popen", cmd_v, mode_v]))
    pipe_ptr = ctx.ensure_slot(f"__listdir_pipe_{id(path_arg)}", PTR)
    ctx.emit(IRInstr("store", None, [pipe_v, pipe_ptr]))

    acc_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", acc_v, ["_abi_new_list", ctx.shared_zero]))
    acc_ptr = ctx.ensure_slot(f"__listdir_acc_{id(path_arg)}", PTR)
    ctx.emit(IRInstr("store", None, [acc_v, acc_ptr]))

    empty_name = ctx.mctx.intern_str("")
    empty_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
    line0_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", line0_v, ["_abi_str_concat_dup", empty_v]))
    line_ptr = ctx.ensure_slot(f"__listdir_line_{id(path_arg)}", PTR)
    ctx.emit(IRInstr("store", None, [line0_v, line_ptr]))

    loop_b = ctx.new_block("listdir_loop")
    nl_b = ctx.new_block("listdir_nl")
    skip_b = ctx.new_block("listdir_skip")
    append_b = ctx.new_block("listdir_append")
    reset_b = ctx.new_block("listdir_reset")
    body_b = ctx.new_block("listdir_body")
    done_b = ctx.new_block("listdir_done")
    ctx.emit(IRInstr("br", None, [loop_b.label]))

    ctx.switch_to(loop_b)
    pipe_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", pipe_v2, [pipe_ptr]))
    c_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", c_v, ["fgetc", pipe_v2]))
    c_ext = ctx.tmp(I64)
    ctx.emit(IRInstr("sext", c_ext, [IRValue(c_v.name, IRType("i32"))]))
    neg1 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", neg1, [-1]))
    is_eof = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_eof, [c_ext, neg1]))
    ctx.emit(IRInstr("br.t", None, [is_eof, done_b.label, body_b.label]))

    ctx.switch_to(body_b)
    ten = ctx.tmp(I64)
    ctx.emit(IRInstr("const", ten, [10]))
    is_nl = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_nl, [c_ext, ten]))
    cr_check_b = ctx.new_block("listdir_crcheck")
    ctx.emit(IRInstr("br.t", None, [is_nl, nl_b.label, cr_check_b.label]))

    ctx.switch_to(cr_check_b)
    thirteen = ctx.tmp(I64)
    ctx.emit(IRInstr("const", thirteen, [13]))
    is_cr = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_cr, [c_ext, thirteen]))
    append_char_b = ctx.new_block("listdir_appendchar")
    ctx.emit(IRInstr("br.t", None, [is_cr, loop_b.label, append_char_b.label]))

    ctx.switch_to(append_char_b)
    ch_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", ch_v, ["_abi_chr", c_ext]))
    cur_line_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_line_v, [line_ptr]))
    new_line_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", new_line_v, ["_abi_str_concat", cur_line_v, ch_v]))
    ctx.emit(IRInstr("store", None, [new_line_v, line_ptr]))
    ctx.emit(IRInstr("br", None, [loop_b.label]))

    ctx.switch_to(nl_b)
    line_for_len_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", line_for_len_v, [line_ptr]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", len_v, ["strlen", line_for_len_v]))
    zero_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero_v, [0]))
    is_empty = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_empty, [len_v, zero_v]))
    ctx.emit(IRInstr("br.t", None, [is_empty, skip_b.label, append_b.label]))

    ctx.switch_to(append_b)
    acc_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", acc_v2, [acc_ptr]))
    line_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", line_v2, [line_ptr]))
    ctx.emit(IRInstr("call", None, ["_abi_list_append", acc_v2, line_v2]))
    ctx.emit(IRInstr("br", None, [skip_b.label]))

    ctx.switch_to(skip_b)
    ctx.emit(IRInstr("br", None, [reset_b.label]))

    ctx.switch_to(reset_b)
    empty_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", empty_v2, [empty_name]))
    fresh_line_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", fresh_line_v, ["_abi_str_concat_dup", empty_v2]))
    ctx.emit(IRInstr("store", None, [fresh_line_v, line_ptr]))
    ctx.emit(IRInstr("br", None, [loop_b.label]))

    ctx.switch_to(done_b)
    pipe_v3 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", pipe_v3, [pipe_ptr]))
    ctx.emit(IRInstr("call", None, ["_pclose", pipe_v3]))
    result_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", result_v, [acc_ptr]))
    return result_v


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


def _emit_closure_value_call(ctx: "_FuncCtx", closure_obj: IRValue, e) -> IRValue:
    """Call an ESCAPING closure VALUE: `[magic, fn_ptr, cap0..capN-1]` whose
    capture count N is not a compile-time fact (the object came from a factory).

    Reads fn_ptr and the runtime count (list length - 2), then branches on N to
    emit a fixed-arity `fn(cap0..capN-1, args...)` call per candidate -- the
    leading-captured-params convention the lifted function expects. Driven by an
    already-lowered object so both spellings share it: a closure held in a
    NAME (`add5 = make_adder(5); add5(10)`) and one produced by an EXPRESSION
    (`adder(5)(10)`), which has no slot to load from.
    """
    MAX_CAPTURES = 8
    buf_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", buf_addr, [closure_obj, _LIST_BUF_OFF]))
    buf = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", buf, [buf_addr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [closure_obj, _LIST_LEN_OFF]))
    list_len = ctx.tmp(I64)
    ctx.emit(IRInstr("load", list_len, [len_addr]))
    two = ctx.tmp(I64)
    ctx.emit(IRInstr("const", two, [2]))
    count = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", count, [list_len, two]))
    fn_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", fn_addr, [buf, 8]))
    fn = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", fn, [fn_addr]))
    call_args = [_lower_expr(ctx, argument) for argument in e.args]
    res_ty = ir_type_for(A.expr_type(e))
    res_ptr = ctx.ensure_slot(f"__clv_res_{id(e)}", res_ty)
    check_blocks = [ctx.new_block(f"clvcheck{n}") for n in range(MAX_CAPTURES + 1)]
    hit_blocks = [ctx.new_block(f"clvhit{n}") for n in range(MAX_CAPTURES + 1)]
    stub_b = ctx.new_block("clvstub")
    end_b = ctx.new_block("clvend")
    ctx.emit(IRInstr("br", None, [check_blocks[0].label]))
    for n in range(MAX_CAPTURES + 1):
        ctx.switch_to(check_blocks[n])
        n_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", n_v, [n]))
        is_match = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", is_match, [count, n_v]))
        nxt = check_blocks[n + 1].label if n < MAX_CAPTURES else stub_b.label
        ctx.emit(IRInstr("br.t", None, [is_match, hit_blocks[n].label, nxt]))
        ctx.switch_to(hit_blocks[n])
        caps: list[IRValue] = []
        for i in range(n):
            cap_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", cap_addr, [buf, (i + 2) * 8]))
            cap_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", cap_v, [cap_addr]))
            caps.append(cap_v)
        mv = ctx.tmp(res_ty)
        ctx.emit(IRInstr("call", mv, [fn, *caps, *call_args]))
        ctx.emit(IRInstr("store", None, [mv, res_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(stub_b)
    stub_v = ctx.tmp(res_ty)
    ctx.emit(IRInstr("const", stub_v, [0]))
    ctx.emit(IRInstr("store", None, [stub_v, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(end_b)
    out = ctx.tmp(res_ty)
    ctx.emit(IRInstr("load", out, [res_ptr]))
    return out


def _value_truthy_typed(
    ctx: _FuncCtx, v: IRValue, t: str, tag: int
) -> IRValue:
    """`_value_truthy` for a value whose static TYPE is known.

    A str or container is falsy on its CONTENTS, not on its allocation
    pointer: `''` is a non-NULL interned pointer, so the plain nonzero test
    called it truthy and `'' or 'a'` evaluated to `''`. `_lower_truthy` already
    gets this right for conditions; this is the same rule for a value that has
    already been lowered (the operand-returning `and`/`or` path, which must not
    evaluate its operand twice).
    """
    if t == "str" or t in ("list", "tuple", "dict", "set"):
        res_ptr = ctx.ensure_slot(f"__vtruthy_{tag}", I64)
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        ctx.emit(IRInstr("store", None, [zero, res_ptr]))
        nonnull_b = ctx.new_block(f"vtruthynn{tag}")
        end_b = ctx.new_block(f"vtruthyend{tag}")
        # Keep the NULL check: an Optional value must not be dereferenced.
        ctx.emit(IRInstr("br.t", None, [v, nonnull_b.label, end_b.label]))
        ctx.switch_to(nonnull_b)
        if t == "str":
            payload = ctx.tmp(I64)
            ctx.emit(IRInstr("call", payload, ["strlen", v]))
        else:
            len_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", len_ptr, [v, _LIST_LEN_OFF]))
            payload = ctx.tmp(I64)
            ctx.emit(IRInstr("load", payload, [len_ptr]))
        ctx.emit(IRInstr("store", None, [payload, res_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))
        ctx.switch_to(end_b)
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("load", out, [res_ptr]))
        return out
    return _value_truthy(ctx, v)


def _lower_truthy(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    """Lower `e` then convert its value to truthy I64 -- see
    `_value_truthy`. Use this (not `_value_truthy` directly) whenever `e`
    hasn't been lowered yet, so it's only evaluated once."""
    if isinstance(e, A.BoolOp):
        # In a condition we only need the boolean result, not Python's
        # operand-returning `and`/`or` value. Lower each side through its own
        # real truthiness rule so mixed shapes such as
        # `text and text.isalnum()` never store an integer 0/1 into a pointer
        # slot and later pass address 1 to strlen().
        result_ptr = ctx.ensure_slot(f"__truthy_boolop_{id(e)}", I64)
        left = _lower_truthy(ctx, e.left)
        rhs_b = ctx.new_block("truthyboolrhs")
        shortcut_b = ctx.new_block("truthyboolshortcut")
        end_b = ctx.new_block("truthyboolend")
        if e.op == "and":
            ctx.emit(
                IRInstr(
                    "br.t",
                    None,
                    [left, rhs_b.label, shortcut_b.label],
                )
            )
            shortcut_value = 0
        elif e.op == "or":
            ctx.emit(
                IRInstr(
                    "br.t",
                    None,
                    [left, shortcut_b.label, rhs_b.label],
                )
            )
            shortcut_value = 1
        else:
            raise LowerError(f"unsupported boolop {e.op!r}")

        ctx.switch_to(shortcut_b)
        constant = ctx.tmp(I64)
        ctx.emit(IRInstr("const", constant, [shortcut_value]))
        ctx.emit(IRInstr("store", None, [constant, result_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(rhs_b)
        right = _lower_truthy(ctx, e.right)
        ctx.emit(IRInstr("store", None, [right, result_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        result = ctx.tmp(I64)
        ctx.emit(IRInstr("load", result, [result_ptr]))
        return result

    t = A.expr_type(e)
    if t == "str" or t in ("list", "tuple", "dict", "set"):
        # Heap containers are falsy based on their contents, not their
        # allocation pointer.  Empty strings are still non-NULL interned
        # pointers, while list/tuple/dict/set objects keep their length at
        # +8.  Keep the NULL check for Optional values before dereferencing.
        value = _lower_expr(ctx, e)
        result_ptr = ctx.ensure_slot(f"__truthy_payload_{id(e)}", I64)
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        ctx.emit(IRInstr("store", None, [zero, result_ptr]))
        nonnull_b = ctx.new_block("truthynonnull")
        end_b = ctx.new_block("truthyend")
        ctx.emit(IRInstr("br.t", None, [value, nonnull_b.label, end_b.label]))

        ctx.switch_to(nonnull_b)
        if t == "str":
            payload = ctx.tmp(I64)
            ctx.emit(IRInstr("call", payload, ["strlen", value]))
        else:
            payload_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", payload_ptr, [value, _LIST_LEN_OFF]))
            payload = ctx.tmp(I64)
            ctx.emit(IRInstr("load", payload, [payload_ptr]))
        ctx.emit(IRInstr("store", None, [payload, result_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        result = ctx.tmp(I64)
        ctx.emit(IRInstr("load", result, [result_ptr]))
        return result
    if t.startswith("instance:"):
        # `if obj:` / `while obj:` on a user instance -- Python truthiness
        # calls `__bool__` (or, if absent, `__len__`) rather than testing
        # the instance POINTER itself, which is always a non-NULL truthy
        # value for any live instance. `_value_truthy`'s generic "any heap
        # pointer passes through as a raw nonzero test" fallback had no
        # dunder awareness at all here (unlike BinOp/UnaryOp/Compare/
        # Call, which all gained their own dunder_owner checks earlier
        # this session) -- confirmed via `369_dunder_bool.py`'s
        # `while c:` (a Counter with __bool__ returning `self.n > 0`)
        # looping forever: the loop condition always saw c's own nonzero
        # pointer and never terminated. Mirrors codegen.py's
        # `_gen_truthy_test` exactly: `__bool__` takes precedence over
        # `__len__` (matches CPython); a call result of 0 is falsy,
        # nonzero is truthy (already the right sense for `__len__`'s
        # count too, and `__bool__`'s own inferred_type is "int" so a
        # real True/False return already lowers to 1/0).
        cls_name = t.split(":", 1)[1]
        for mname in ("__bool__", "__len__"):
            owner = _resolve_method_owner(ctx, cls_name, mname)
            if owner is not None:
                obj_v = _lower_expr(ctx, e)
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, [f"{owner}__{mname}", obj_v]))
                return v
        # No __bool__/__len__: any live (non-NULL) instance is truthy --
        # still must evaluate `e` for its side effects even though the
        # result is unconditionally truthy.
        _lower_expr(ctx, e)
        one = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one, [1]))
        return one
    return _value_truthy(ctx, _lower_expr(ctx, e))


def _resolve_class_chain(ctx: _FuncCtx, name: str) -> list[str]:
    """The classes to search for a member, `name` first.

    Depth-first, left to right: each class's own `parent` chain, then its EXTRA
    bases (`class C(A, B)`) in declaration order. Without the extra bases a
    mixin's methods resolved in sema but the call site emitted `C__b` for a
    method that only exists as `B__b`, failing at link time.
    """
    out: list[str] = []
    stack: list[str] = [name]
    while stack:
        cur = stack.pop(0)
        if cur is None or cur in out:
            continue
        out.append(cur)
        sig = ctx.mctx.classes_sig.get(cur)
        if sig is None:
            continue
        _nexts: list = []
        if sig.parent is not None:
            _nexts.append(sig.parent)
        for _eb in getattr(sig, "extra_bases", []) or []:
            _nexts.append(_eb)
        stack = _nexts + stack
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


def _classes_resolving_method(ctx: _FuncCtx, method: str) -> list[tuple[int, str]]:
    """[(class_id, owner)] for EVERY user class (regardless of hierarchy)
    whose chain resolves `method` -- the whole-program candidate set for a
    method call on an opaque/`any` receiver whose static type names no class.

    Unlike `_virtual_dispatch_rows`, which is rooted at a known base class,
    this spans all classes: a `list[object]` element (or any `object`-typed
    value) may at runtime be an instance of any class that happens to define
    `method`, so the runtime dispatch must consider them all. Each row's
    class_id is the same `__class__` tag `_lower_read_any_tag` reads back, so
    an equality chain over these rows recovers the exact concrete method to
    call -- real virtual dispatch, not the old graceful no-op stub.
    """
    rows: list[tuple[int, str]] = []
    for cname, cid in ctx.mctx.class_ids.items():
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


def _lower_list_instance_repr(ctx: _FuncCtx, e, obj_v: IRValue, owner: str, method: str) -> IRValue:
    """repr of a `list[instance:X]` as `[<x0 repr>, <x1 repr>, ...]`.

    The `_abi_list_repr` runtime helper formats each cell by a fixed kind tag
    and has no way to call a user-defined `__repr__`/`__str__`, so an instance
    list otherwise prints raw element pointers. Build the text here instead: a
    compile-time loop that calls `X`'s resolved dunder per element and joins the
    results with ", " inside brackets. `owner`/`method` come from
    `_resolve_str_dunder`, so the dispatch matches a single instance's repr.
    """
    res_ptr = ctx.ensure_slot(f"__lirep_res_{id(e)}", PTR)
    open_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", open_v, [ctx.mctx.intern_str("[")]))
    ctx.emit(IRInstr("store", None, [open_v, res_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    idx_ptr = ctx.ensure_slot(f"__lirep_idx_{id(e)}", I64)
    zero0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero0, [0]))
    ctx.emit(IRInstr("store", None, [zero0, idx_ptr]))
    # Block-creation order matters for regalloc liveness (see this file's
    # comprehension-loop note): head/body first, the inner sep/elt blocks next,
    # and the loop-exit `end` block LAST.
    head_b = ctx.new_block("lirephead")
    body_b = ctx.new_block("lirepbody")
    sep_b = ctx.new_block("lirepsep")
    elt_b = ctx.new_block("lirepelt")
    end_b = ctx.new_block("lirepend")
    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    go = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", go, [i_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [go, body_b.label, end_b.label]))
    # body: emit ", " before every element after the first
    ctx.switch_to(body_b)
    bi = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi, [idx_ptr]))
    zc = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zc, [0]))
    is_first = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_first, [bi, zc]))
    ctx.emit(IRInstr("br.t", None, [is_first, elt_b.label, sep_b.label]))
    ctx.switch_to(sep_b)
    cs = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cs, [res_ptr]))
    comma = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", comma, [ctx.mctx.intern_str(", ")]))
    cs2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cs2, ["_abi_str_concat", cs, comma]))
    ctx.emit(IRInstr("store", None, [cs2, res_ptr]))
    ctx.emit(IRInstr("br", None, [elt_b.label]))
    # elt: append this element's repr
    ctx.switch_to(elt_b)
    bi2 = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi2, [idx_ptr]))
    ea = _list_elem_addr(ctx, obj_v, bi2)
    elem = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", elem, [ea]))
    er = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", er, [f"{owner}__{method}", elem]))
    cs3 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cs3, [res_ptr]))
    cs4 = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cs4, ["_abi_str_concat", cs3, er]))
    ctx.emit(IRInstr("store", None, [cs4, res_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    ni = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", ni, [bi2, one]))
    ctx.emit(IRInstr("store", None, [ni, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))
    # end: close bracket
    ctx.switch_to(end_b)
    fin = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", fin, [res_ptr]))
    close = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", close, [ctx.mctx.intern_str("]")]))
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out, ["_abi_str_concat", fin, close]))
    return out


def _dict_value_repr_kind(e: A.Expr) -> int:
    vt = getattr(e, "value_type", "int") or "int"
    inner = getattr(e, "inner_value_type", "int") or "int"
    return _composite_repr_kind(vt, inner)


def _lower_dict_key(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    """Lower a dict key expression to the STRING it's stored under.

    asmpython's dict runtime is string-keyed (keys are strdup'd and hashed as
    C strings -- see codegen.py's `_runtime_dict_set`). Every other key type is
    encoded to a canonical string here so a value-equal key always maps to the
    same slot:
      * str / any  -> used directly (an "any" key is already a real pointer;
                      the runtime hashes whatever string it points at).
      * int        -> its decimal spelling (`_abi_int_to_base`).
      * everything else (float / bool / tuple / a nested container / an
        instance) -> its `repr()` string, via the same `_lower_expr_as_str`
        machinery f-strings/`repr()` already use. repr_mode makes the encoding
        UNAMBIGUOUS across kinds: a str key `"1"` reprs to `'1'` while an int
        key `1` encodes to `1`, so they never collide; a tuple key
        `(1, 2)` reprs to its canonical `(1, 2)`. This is exactly what a
        by-value-hashable key (a tuple of scalars -- e.g. an lru_cache key
        `(args, sorted_kwargs)`) needs: value-equal tuples produce identical
        repr strings and therefore hit the same slot.
    """
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
    # float / bool / tuple / list / dict / set / instance: encode by repr, a
    # deterministic canonical string per value. repr_mode keeps str vs non-str
    # keys distinguishable (a str key is quoted, an int/tuple key is not).
    return _lower_expr_as_str(ctx, e, repr_mode=True)


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


def _collect_declared_nonlocals(stmts: list, out: set[str]) -> None:
    for s in stmts:
        if isinstance(s, A.Nonlocal):
            out.update(s.names)
        elif isinstance(s, A.If):
            _collect_declared_nonlocals(s.then, out)
            _collect_declared_nonlocals(s.orelse, out)
        elif isinstance(s, (A.While, A.For)):
            _collect_declared_nonlocals(s.body, out)
            _collect_declared_nonlocals(s.orelse, out)
        elif isinstance(s, A.With):
            _collect_declared_nonlocals(s.body, out)
        elif isinstance(s, A.Try):
            _collect_declared_nonlocals(s.body, out)
            _collect_declared_nonlocals(s.handler, out)
            for _types, _bind, hbody in s.extra_handlers:
                _collect_declared_nonlocals(hbody, out)
            _collect_declared_nonlocals(s.else_body, out)
            _collect_declared_nonlocals(s.finally_body, out)


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
        elif isinstance(s, A.ClosureBind):
            out.add(s.func_name)
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


def _name_value_ptr(ctx: _FuncCtx, name: str, ty: IRType) -> IRValue:
    """Storage containing a name's value, dereferencing closure boxes."""
    ptr = _name_ptr(ctx, name, ty)
    if name not in ctx.nonlocal_names and name not in ctx.boxed_names:
        return ptr
    if name in ctx.boxed_names:
        ptr = ctx.ensure_slot(f"__nl_box_{name}", PTR)
    box = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", box, [ptr]))
    return box


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
    _kinds = A.tuple_element_types(e)
    if not _kinds:
        # A tuple with no static slot shape -- `tuple(xs)` over a list whose
        # LENGTH is a runtime value, which is what the transpose idiom
        # (`zip(*rows)`) and every other tuple-from-iterable produces. The
        # unrolled formatter has nothing to unroll there and emitted a bare
        # "()", silently printing a populated tuple as empty. Walk the real
        # length instead, formatting each slot by the source's element kind.
        return _emit_tuple_repr_dynamic(
            ctx, _lower_expr(ctx, e), getattr(e, "list_el_type", "any"), e
        )
    return _emit_tuple_repr_value(ctx, _lower_expr(ctx, e), _kinds)


def _emit_tuple_repr_dynamic(
    ctx: _FuncCtx, obj_v: IRValue, el_kind: str, e
) -> IRValue:
    """repr of a tuple whose LENGTH is only known at runtime, as "(a, b)".

    Same loop shape as `_lower_list_of_tuples_repr`, but bracketed as a tuple
    and with CPython's 1-element trailing comma. Every slot formats with the
    same kind, which is what a tuple built from an iterable actually has.
    """
    if el_kind in ("", "any", "int"):
        el_kind = "int" if el_kind in ("", "int") else "any"
    res_ptr = ctx.ensure_slot(f"__dtrep_res_{id(e)}", PTR)
    open_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", open_v, [ctx.mctx.intern_str("(")]))
    ctx.emit(IRInstr("store", None, [open_v, res_ptr]))
    buf_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", buf_addr, [obj_v, _LIST_BUF_OFF]))
    buf_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", buf_v, [buf_addr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    idx_ptr = ctx.ensure_slot(f"__dtrep_idx_{id(e)}", I64)
    z0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z0, [0]))
    ctx.emit(IRInstr("store", None, [z0, idx_ptr]))
    h_b = ctx.new_block("dtrephead")
    b_b = ctx.new_block("dtrepbody")
    sep_b = ctx.new_block("dtrepsep")
    elt_b = ctx.new_block("dtrepelt")
    e_b = ctx.new_block("dtrepend")
    ctx.emit(IRInstr("br", None, [h_b.label]))
    ctx.switch_to(h_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    go_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", go_v, [i_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [go_v, b_b.label, e_b.label]))
    ctx.switch_to(b_b)
    bi_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi_v, [idx_ptr]))
    zc = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zc, [0]))
    first_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", first_v, [bi_v, zc]))
    ctx.emit(IRInstr("br.t", None, [first_v, elt_b.label, sep_b.label]))
    ctx.switch_to(sep_b)
    cur_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_v, [res_ptr]))
    comma_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", comma_v, [ctx.mctx.intern_str(", ")]))
    withsep_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", withsep_v, ["_abi_str_concat", cur_v, comma_v]))
    ctx.emit(IRInstr("store", None, [withsep_v, res_ptr]))
    ctx.emit(IRInstr("br", None, [elt_b.label]))
    ctx.switch_to(elt_b)
    ei_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", ei_v, [idx_ptr]))
    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [8]))
    off_v = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", off_v, [ei_v, eight]))
    slot_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", slot_addr, [buf_v, off_v]))
    slot_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", slot_v, [slot_addr]))
    kind_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", kind_v, [_value_repr_kind(el_kind)]))
    txt_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", txt_v, ["_abi_fmt_elem", slot_v, kind_v]))
    prev_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", prev_v, [res_ptr]))
    app_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", app_v, ["_abi_str_concat", prev_v, txt_v]))
    ctx.emit(IRInstr("store", None, [app_v, res_ptr]))
    ni_v = ctx.tmp(I64)
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    ctx.emit(IRInstr("iadd", ni_v, [ei_v, one_v]))
    ctx.emit(IRInstr("store", None, [ni_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [h_b.label]))
    ctx.switch_to(e_b)
    # CPython writes a 1-tuple as "(x,)"; pick the closer at runtime since the
    # length is not a compile-time fact here.
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    is_single = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_single, [len_v, one2]))
    close_ptr = ctx.ensure_slot(f"__dtrep_close_{id(e)}", PTR)
    s1_b = ctx.new_block("dtrepone")
    sn_b = ctx.new_block("dtrepmany")
    fin_b = ctx.new_block("dtrepfin")
    ctx.emit(IRInstr("br.t", None, [is_single, s1_b.label, sn_b.label]))
    ctx.switch_to(s1_b)
    c1_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", c1_v, [ctx.mctx.intern_str(",)")]))
    ctx.emit(IRInstr("store", None, [c1_v, close_ptr]))
    ctx.emit(IRInstr("br", None, [fin_b.label]))
    ctx.switch_to(sn_b)
    cn_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", cn_v, [ctx.mctx.intern_str(")")]))
    ctx.emit(IRInstr("store", None, [cn_v, close_ptr]))
    ctx.emit(IRInstr("br", None, [fin_b.label]))
    ctx.switch_to(fin_b)
    body_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_v, [res_ptr]))
    cl_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cl_v, [close_ptr]))
    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_str_concat", body_v, cl_v]))
    return out_v


def _emit_tuple_repr_value(ctx: _FuncCtx, obj: IRValue, kinds: list) -> IRValue:
    """The body of `_lower_tuple_repr`, but driven by an already-lowered tuple
    VALUE plus its per-slot kinds -- so a caller that has a tuple in hand at
    runtime (each element of a list[tuple], say) can format it the same way
    instead of only working from an AST node."""
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
        fmt_v = elem_v
        if k == "float":
            # _abi_fmt_elem expects a float element's raw bits pre-moved
            # into a GP register (the ad-hoc convention codegen.py's own
            # inline movq-to-rax uses) -- this pipeline's `call` op
            # otherwise routes an F64-typed IR value through an XMM
            # argument register instead, an ABI mismatch. Same bitcast
            # pattern used everywhere else in this file a float value
            # needs to cross into a GP-only call convention (dict/list
            # storage, etc).
            iv = ctx.tmp(I64)
            ctx.emit(IRInstr("bitcast_f2i", iv, [elem_v]))
            fmt_v = iv
        kind_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", kind_v, [_value_repr_kind(k)]))
        elem_repr = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", elem_repr, ["_abi_fmt_elem", fmt_v, kind_v]))
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


def _lower_dict_of_tuples_repr(
    ctx: _FuncCtx, e, obj_v: IRValue, slots: list
) -> IRValue:
    """repr of a dict whose VALUES are tuples, as `{'k': (1, 2, 3)}`.

    Walks the key list, formatting each key as a quoted str and each value with
    the per-slot tuple formatter (or the runtime-length one when the slot shape
    isn't statically known). Same shape as `_lower_list_of_tuples_repr`, which
    exists for the same reason on the list side.
    """
    res_ptr = ctx.ensure_slot(f"__dtrep2_{id(e)}", PTR)
    open_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", open_v, [ctx.mctx.intern_str("{")]))
    ctx.emit(IRInstr("store", None, [open_v, res_ptr]))

    keys_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", obj_v]))
    kbuf_a = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", kbuf_a, [keys_v, _LIST_BUF_OFF]))
    kbuf = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", kbuf, [kbuf_a]))
    klen_a = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", klen_a, [keys_v, _LIST_LEN_OFF]))
    klen = ctx.tmp(I64)
    ctx.emit(IRInstr("load", klen, [klen_a]))

    idx_ptr = ctx.ensure_slot(f"__dtrep2i_{id(e)}", I64)
    z0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z0, [0]))
    ctx.emit(IRInstr("store", None, [z0, idx_ptr]))

    h_b = ctx.new_block("dtr2head")
    b_b = ctx.new_block("dtr2body")
    sep_b = ctx.new_block("dtr2sep")
    ent_b = ctx.new_block("dtr2ent")
    e_b = ctx.new_block("dtr2end")
    ctx.emit(IRInstr("br", None, [h_b.label]))

    ctx.switch_to(h_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    go_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", go_v, [i_v, klen]))
    ctx.emit(IRInstr("br.t", None, [go_v, b_b.label, e_b.label]))

    ctx.switch_to(b_b)
    bi_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi_v, [idx_ptr]))
    zc = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zc, [0]))
    first_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", first_v, [bi_v, zc]))
    ctx.emit(IRInstr("br.t", None, [first_v, ent_b.label, sep_b.label]))

    ctx.switch_to(sep_b)
    cur_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_v, [res_ptr]))
    comma_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", comma_v, [ctx.mctx.intern_str(", ")]))
    ws_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", ws_v, ["_abi_str_concat", cur_v, comma_v]))
    ctx.emit(IRInstr("store", None, [ws_v, res_ptr]))
    ctx.emit(IRInstr("br", None, [ent_b.label]))

    ctx.switch_to(ent_b)
    ei_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", ei_v, [idx_ptr]))
    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [8]))
    koff = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", koff, [ei_v, eight]))
    k_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", k_addr, [kbuf, koff]))
    k_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", k_v, [k_addr]))
    kkind = ctx.tmp(I64)
    ctx.emit(IRInstr("const", kkind, [1]))  # keys are stored as strings
    ktxt = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", ktxt, ["_abi_fmt_elem", k_v, kkind]))
    prev_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", prev_v, [res_ptr]))
    wk_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", wk_v, ["_abi_str_concat", prev_v, ktxt]))
    colon_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", colon_v, [ctx.mctx.intern_str(": ")]))
    wc_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", wc_v, ["_abi_str_concat", wk_v, colon_v]))
    dflt = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", dflt, [0]))
    val_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", val_v, ["_abi_dict_get_default", obj_v, k_v, dflt]))
    if slots:
        # An unrolled formatter: creates no blocks, so values stay live.
        vtxt = _emit_tuple_repr_value(ctx, val_v, slots)
        _acc_v = wc_v
        _idx_now = ei_v
    else:
        # The runtime-length formatter is a LOOP. Spill everything the rest of
        # this block needs before it and reload after: a value held in a
        # register across a construct that creates blocks is live across
        # back-edges that did not exist when it was defined, which is exactly
        # what made the recursive sequence comparison loop forever.
        ctx.emit(IRInstr("store", None, [wc_v, res_ptr]))
        vtxt = _emit_tuple_repr_dynamic(ctx, val_v, "any", e)
        _acc_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", _acc_v, [res_ptr]))
        _idx_now = ctx.tmp(I64)
        ctx.emit(IRInstr("load", _idx_now, [idx_ptr]))
    after_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", after_v, ["_abi_str_concat", _acc_v, vtxt]))
    ctx.emit(IRInstr("store", None, [after_v, res_ptr]))
    ni_v = ctx.tmp(I64)
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    ctx.emit(IRInstr("iadd", ni_v, [_idx_now, one_v]))
    ctx.emit(IRInstr("store", None, [ni_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [h_b.label]))

    ctx.switch_to(e_b)
    body_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", body_v, [res_ptr]))
    close_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", close_v, [ctx.mctx.intern_str("}")]))
    out_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out_v, ["_abi_str_concat", body_v, close_v]))
    return out_v


def _lower_list_of_tuples_repr(ctx: _FuncCtx, e, obj_v: IRValue, slots: list) -> IRValue:
    """repr of a `list[tuple]` as `[(a, b), (c, d)]`, formatting every slot by
    its own static kind.

    `_abi_list_repr`'s tuple element kind (5) is hard-coded for the dict-items
    shape -- a (str, int) pair -- because that is what it was written for. Any
    other slot layout was misread: an (int, str) pair had its leading int
    formatted as a string POINTER, which segfaults (`print([(1,'b')])` crashes
    outright), and other layouts silently printed pointer numbers. Build the
    text here instead, reusing the per-slot tuple formatter.
    """
    res_ptr = ctx.ensure_slot(f"__ltrep_res_{id(e)}", PTR)
    open_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", open_v, [ctx.mctx.intern_str("[")]))
    ctx.emit(IRInstr("store", None, [open_v, res_ptr]))
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    idx_ptr = ctx.ensure_slot(f"__ltrep_idx_{id(e)}", I64)
    z0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z0, [0]))
    ctx.emit(IRInstr("store", None, [z0, idx_ptr]))
    h_b = ctx.new_block("ltrephead")
    b_b = ctx.new_block("ltrepbody")
    sep_b = ctx.new_block("ltrepsep")
    elt_b = ctx.new_block("ltrepelt")
    e_b = ctx.new_block("ltrepend")
    ctx.emit(IRInstr("br", None, [h_b.label]))
    ctx.switch_to(h_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    go_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", go_v, [i_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [go_v, b_b.label, e_b.label]))
    ctx.switch_to(b_b)
    bi_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi_v, [idx_ptr]))
    zc = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zc, [0]))
    first_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", first_v, [bi_v, zc]))
    ctx.emit(IRInstr("br.t", None, [first_v, elt_b.label, sep_b.label]))
    ctx.switch_to(sep_b)
    cs = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cs, [res_ptr]))
    comma_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", comma_v, [ctx.mctx.intern_str(", ")]))
    cs2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cs2, ["_abi_str_concat", cs, comma_v]))
    ctx.emit(IRInstr("store", None, [cs2, res_ptr]))
    ctx.emit(IRInstr("br", None, [elt_b.label]))
    ctx.switch_to(elt_b)
    bi2_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi2_v, [idx_ptr]))
    ea = _list_elem_addr(ctx, obj_v, bi2_v)
    tup_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", tup_v, [ea]))
    tr_v = _emit_tuple_repr_value(ctx, tup_v, list(slots))
    cs3 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cs3, [res_ptr]))
    cs4 = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cs4, ["_abi_str_concat", cs3, tr_v]))
    ctx.emit(IRInstr("store", None, [cs4, res_ptr]))
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    ni_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", ni_v, [bi2_v, one_v]))
    ctx.emit(IRInstr("store", None, [ni_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [h_b.label]))
    ctx.switch_to(e_b)
    fin_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", fin_v, [res_ptr]))
    close2_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", close2_v, [ctx.mctx.intern_str("]")]))
    out2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out2, ["_abi_str_concat", fin_v, close2_v]))
    return out2


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


def _lower_str_format(ctx: _FuncCtx, e: A.MethodCall) -> IRValue:
    """`"...".format(args)` with a literal format string -- entirely
    unimplemented before this. Ports codegen.py's `_gen_str_format`
    exactly: parse the literal into (lit-text | arg-reference) pieces
    via `A.parse_format_fields` (a shared parser sema's own validation
    pass also uses, so the two stay in sync), then for each arg-
    reference piece, stamp `fmt_spec`/`conv_flag` onto the referenced
    argument expression and reuse `_lower_fstring_segment` -- the exact
    same per-segment formatting f-strings already use, since `.format()`
    supports the identical `[[fill]align]width.precision` mini-language
    and `!r`/`!s`/`!a` conversions. Only supports a literal format
    string (`e.obj` is `A.StrLit`) -- caller only dispatches here in
    that case; a `.format()` call on a general str expression falls
    through to the generic str-method dispatch and its "unknown method"
    error, matching codegen.py's own scope (a runtime-computed format
    string would need the mini-language parsed at RUNTIME, a much
    larger feature no caller in this test suite needs).
    """
    fmt = e.obj.value  # type: ignore[attr-defined]
    pieces = A.parse_format_fields(fmt)
    if not pieces:
        empty = ctx.mctx.intern_str("")
        out = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", out, [empty]))
        return out

    def emit_piece(kind, val, spec, conv) -> IRValue:
        if kind == "lit":
            name = ctx.mctx.intern_str(val)
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", v, [name]))
            return v
        if isinstance(val, str):
            arg = None
            for kw_name, kw_arg in e.kwargs or []:
                if kw_name == val:
                    arg = kw_arg
                    break
        else:
            arg = e.args[val]
        arg.fmt_spec = spec  # type: ignore[attr-defined]
        arg.conv_flag = conv  # type: ignore[attr-defined]
        return _lower_fstring_segment(ctx, arg)

    acc = emit_piece(*pieces[0])
    for kind, val, spec, conv in pieces[1:]:
        piece_v = emit_piece(kind, val, spec, conv)
        new_acc = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", new_acc, ["_abi_str_concat", acc, piece_v]))
        acc = new_acc
    return acc


def _lower_expr_as_str(ctx: _FuncCtx, e: A.Expr, repr_mode: bool = False) -> IRValue:
    ty = A.expr_type(e)
    if isinstance(e, A.Name) and e.name in ctx.narrowed_types:
        # `print(value)`/f-string/str()/repr() on a variable narrowed by an
        # enclosing `if type(value) is T:`/`if isinstance(value, T):` (see
        # `_narrowed_if_type`) -- format it as T, the concrete type the
        # runtime check already proved, instead of falling through to the
        # "any"-typed default further below (which silently assumes int,
        # the same pre-existing, separately-scoped gap this whole feature
        # was careful not to depend on being fixed -- narrowing sidesteps
        # it here by supplying a real static type where one is actually
        # known, rather than trying to fix the "any" fallback in general).
        #
        # Deliberately does NOT gate on `ty == "any"` first: sema stamps
        # `inferred_type` per AST NODE, not per variable, so two different
        # `A.Name("value")` occurrences for the same parameter can carry
        # different static types depending on where they sit in the
        # source (confirmed via a repro where this exact print()'s own
        # `value` argument was independently typed "int", the unrelated
        # unknown-sentinel default, even though the enclosing
        # `isinstance(value, bool)` check right above it proved the
        # runtime kind). Being in `ctx.narrowed_types` at all is the
        # authoritative signal that this read is inside a branch that
        # already proved the concrete kind, regardless of what this
        # PARTICULAR node's own (possibly stale/unrelated) static type
        # says.
        ty = ctx.narrowed_types[e.name]
    if ty == "bool":
        # Mirrors the `A.is_bool_expr(e)` branch under `ty == "int"` below
        # exactly, just reached directly: a narrowed name has no AST-level
        # `is_bool` flag of its own to trigger that branch (it's a runtime
        # fact proven by the enclosing type check, not a literal the
        # parser tagged), so this is the same runtime true/false-string
        # dispatch, inlined for the "bool" narrowed type specifically.
        n_v = _lower_expr(ctx, e)
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        is_zero = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", is_zero, [n_v, zero]))
        false_b = ctx.new_block("narrowboolstrfalse")
        true_b = ctx.new_block("narrowboolstrtrue")
        end_b = ctx.new_block("narrowboolstrend")
        res_ptr = ctx.ensure_slot(f"__narrowbool_str_{id(e)}", PTR)
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
        _el = getattr(e, "list_el_type", "int") or "int"
        if isinstance(e, A.ListLit):
            _el = e.el_type or "int"
        if _el.startswith("instance:"):
            # A list of user instances: elements are repr'd via the class's
            # own __repr__/__str__ (containers always use repr for elements),
            # which the _abi_list_repr runtime helper can't call.
            _rd = _resolve_str_dunder(ctx, _el.split(":", 1)[1], repr_first=True)
            if _rd is not None:
                return _lower_list_instance_repr(ctx, e, obj, _rd[0], _rd[1])
        if _el == "tuple":
            # A list of tuples with known slot kinds: format each slot by its
            # own kind. `_abi_list_repr`'s tuple kind assumes the dict-items
            # (str, int) layout and misreads anything else -- see
            # `_lower_list_of_tuples_repr`.
            _slots = (
                list(getattr(e, "el_tuple_types", []) or [])
                or list(getattr(e, "tuple_elem_types", []) or [])
            )
            if _slots:
                return _lower_list_of_tuples_repr(ctx, e, obj, _slots)
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
        _dvt = getattr(e, "value_type", "int") or "int"
        if _dvt == "tuple":
            # `_abi_dict_repr`'s tuple value kind is hard-coded for the
            # dict-items (str, int) pair layout, so `{'k': (1, 2, 3)}` had its
            # first slot formatted as a string POINTER and crashed -- the same
            # assumption that made a list[tuple] fault before it got its own
            # compiler-side formatter. Build the text here instead, reusing the
            # runtime-length tuple formatter for each value.
            _dslots = list(getattr(e, "value_tuple_elem_types", []) or [])
            return _lower_dict_of_tuples_repr(ctx, e, obj, _dslots)
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
    if ty == "any":
        # An opaque value that may be a boxed scalar: format on its runtime
        # tag from the RAW (still-boxed) cell (`_lower_expr_inner`). A
        # never-boxed value falls through to the same `_abi_fmt_elem` path
        # inside the formatter, unchanged. repr_mode adds the surrounding
        # quotes for a boxed str (int/float/bool/None repr == str).
        raw = _lower_expr_inner(ctx, e)
        return _lower_format_any_value(ctx, raw, repr_mode=repr_mode)
    val = _lower_expr(ctx, e)
    kind = ctx.tmp(I64)
    ctx.emit(IRInstr("const", kind, [0]))
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", out, ["_abi_fmt_elem", val, kind]))
    return out


def _lower_type_name_attr(ctx: _FuncCtx, e: A.Attr) -> IRValue:
    arg = e.obj.args[0]
    arg_t = A.expr_type(arg)
    if arg_t != "any":
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
    # `type(x).__name__` where x's STATIC type is "any" -- same runtime
    # tag dispatch as the `type()` call site itself (see its "any" branch
    # for the full rationale), just without the "<class '...'>" wrapping.
    obj_v = _lower_expr_inner(ctx, arg)
    tag_v = _lower_read_any_tag(ctx, obj_v)
    out_ptr = ctx.ensure_slot(f"__typename_out_{id(e)}", PTR)
    empty_sym = ctx.mctx.intern_str("")
    empty_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", empty_v, [empty_sym]))
    ctx.emit(IRInstr("store", None, [empty_v, out_ptr]))
    end_b = ctx.new_block("typenameend")
    name_by_tag: list = [
        ("bool", BUILTIN_TYPE_IDS["bool"]),
        ("NoneType", NONE_TYPE_ID),
        ("int", BUILTIN_TYPE_IDS["int"]),
        ("float", BUILTIN_TYPE_IDS["float"]),
        ("str", BUILTIN_TYPE_IDS["str"]),
    ]
    # A user-class instance carries a positive RTTI class id as its tag --
    # dispatch each one to its own class NAME too (not just the builtin
    # scalars above), so `type(instance).__name__` recovers "MyClass"
    # instead of falling through to "". Required for the class-keyed-dict
    # lowering (see sema's `_rewrite_class_keyed_dicts`), whose rewritten
    # `D[type(x).__name__]` key must equal the string key "MyClass" the
    # dict was rewritten to use.
    for cname, cid in ctx.mctx.class_ids.items():
        name_by_tag.append((cname, cid))
    for kind, tag in name_by_tag:
        match_b = ctx.new_block(f"typename_{kind}")
        next_b = ctx.new_block(f"typenamenext_{kind}")
        tag_const = ctx.tmp(I64)
        ctx.emit(IRInstr("const", tag_const, [tag]))
        eq_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", eq_v, [tag_v, tag_const]))
        ctx.emit(IRInstr("br.t", None, [eq_v, match_b.label, next_b.label]))
        ctx.switch_to(match_b)
        text_sym = ctx.mctx.intern_str(kind)
        text_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", text_v, [text_sym]))
        ctx.emit(IRInstr("store", None, [text_v, out_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))
        ctx.switch_to(next_b)
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(end_b)
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", out, [out_ptr]))
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
    # `raise_b` created BEFORE `ok_b` deliberately -- its own `br` back to
    # `ok_b` is otherwise a branch to a LOWER block-list index, which
    # regalloc.py's `_last_uses` loop-back-edge detection (see its own
    # docstring) mistakes for a genuine loop back-edge, spuriously
    # force-extending the liveness of every value touched anywhere in
    # that "loop" range -- for a function chaining several `//`/`%`
    # expressions (e.g. calendar.py's `weekday()`: `(day + (13 * (month
    # + 1)) // 5 + k + k // 4 + j // 4 - 2 * j) % 7`, four divisions in
    # one expression), each division's own divzero-check pair sits at a
    # HIGHER block index than the previous one's, so this false "loop"
    # detection chained across them ends up scrambling which physical
    # register still holds an earlier division's result by the time a
    # LATER division reads it as its own dividend/divisor -- confirmed
    # via gdb + a git-checkout A/B diff against the immediately prior
    # commit (before this zero-check existed at all): `weekday()` reads
    # a divisor of 0 that was never 0 in the source, a genuinely
    # different value's register bleeding through. Creating `raise_b`
    # first makes `ok_b`'s index the higher one, so `raise_b`'s `br`
    # becomes an ordinary FORWARD edge -- not a loop by any definition,
    # and never falsely detected as one. (`raise_b` is dead code on
    # every real execution path in practice -- `_abi_raise` either
    # longjmps to an active handler or exits the process -- but the IR
    # still needs a terminator for it.)
    raise_b = ctx.new_block(f"divzero_raise_{tag}")
    ok_b = ctx.new_block(f"divzero_ok_{tag}")
    ctx.emit(IRInstr("br.t", None, [nonzero, ok_b.label, raise_b.label]))

    ctx.switch_to(raise_b)
    msg_name = ctx.mctx.intern_str("division by zero")
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["ZeroDivisionError"]]))
    ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
    ctx.emit(IRInstr("br", None, [ok_b.label]))

    ctx.switch_to(ok_b)


def _emit_float_divzero_check(ctx: _FuncCtx, divisor: IRValue, op: str, tag: int) -> None:
    """Raise ZeroDivisionError if `divisor` (an F64) is 0.0, for float
    `/`/`//`/`%`. Unlike int division (a hardware SIGFPE), a float divide
    by zero is well-defined IEEE-754 (inf/nan) and doesn't crash on its
    own -- but Python raises ZeroDivisionError for all three of these
    operators on floats too (`5.0 / 0.0`, `5.0 // 0.0`, `5.0 % 0.0` all
    raise), so this was a real correctness gap, not a crash-safety one:
    confirmed via a real repro that these three silently returned
    `inf`/`inf`/`nan` instead of raising a catchable exception. Message
    text is the same plain `"division by zero"` int-division uses --
    an earlier version of this function used per-operator CPython-
    historical message variants (`"float division by zero"` etc.),
    which turned out to be WRONG for the CPython version this project
    targets: verified directly against the live interpreter (`try:
    5.0/0.0 ... except ZeroDivisionError as e: print(e)` and the `//`/`%`
    equivalents) that all three print the identical plain message, no
    "float"/operator-specific variant at all. `op` is kept as a
    parameter (unused for the message now) since callers already pass
    it and it documents which operator triggered the check. Same
    raise_b-before-ok_b block-ordering rule as `_emit_int_divzero_check`
    (see its own docstring).
    """
    zero = ctx.tmp(F64)
    ctx.emit(IRInstr("const", zero, [0.0]))
    nonzero = ctx.tmp(I64)
    ctx.emit(IRInstr("fcmp.ne", nonzero, [divisor, zero]))
    raise_b = ctx.new_block(f"fdivzero_raise_{tag}")
    ok_b = ctx.new_block(f"fdivzero_ok_{tag}")
    ctx.emit(IRInstr("br.t", None, [nonzero, ok_b.label, raise_b.label]))

    ctx.switch_to(raise_b)
    msg_name = ctx.mctx.intern_str("division by zero")
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["ZeroDivisionError"]]))
    ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
    ctx.emit(IRInstr("br", None, [ok_b.label]))

    ctx.switch_to(ok_b)


def _emit_list_index_bounds_check(ctx: _FuncCtx, list_v: IRValue, idx_v: IRValue, tag: int) -> None:
    """Raise IndexError("list index out of range") if idx_v (BEFORE
    Python's negative-index wraparound -- matches CPython's own check,
    which happens against the un-wrapped index range) is out of
    [-len, len). Only wired into the plain `lst[i]` READ subscript site
    (A.Subscript's list/tuple case) -- `_list_elem_addr` itself stays
    unchecked, matching codegen.py's documented silent-corrupt-on-OOB
    behavior, since it also backs many internal loop helpers (list.pop,
    slicing, etc.) that only ever call it with indices already known to
    be in range; adding a check there would be redundant overhead on
    every one of those, not just user-facing reads. `raise_b` is created
    BEFORE `ok_b` deliberately, same reasoning as
    `_emit_int_divzero_check`'s own docstring (regalloc.py's `_last_uses`
    loop-back-edge heuristic misdetects a lower-block-index target as a
    loop otherwise)."""
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [list_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    neg_len = ctx.tmp(I64)
    ctx.emit(IRInstr("ineg", neg_len, [len_v]))
    ge_lo = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.ge", ge_lo, [idx_v, neg_len]))
    lt_hi = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", lt_hi, [idx_v, len_v]))
    in_range = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", in_range, [ge_lo, lt_hi]))

    raise_b = ctx.new_block(f"idxoob_raise_{tag}")
    ok_b = ctx.new_block(f"idxoob_ok_{tag}")
    ctx.emit(IRInstr("br.t", None, [in_range, ok_b.label, raise_b.label]))

    ctx.switch_to(raise_b)
    msg_name = ctx.mctx.intern_str("list index out of range")
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["IndexError"]]))
    ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
    ctx.emit(IRInstr("br", None, [ok_b.label]))

    ctx.switch_to(ok_b)


def _emit_str_index_check(ctx: _FuncCtx, idx_v: IRValue, tag: int) -> None:
    """Raise ValueError("substring not found") if idx_v == -1 -- backs
    str.index()/str.rindex(), which (unlike find()/rfind()) raise instead
    of returning -1 on a miss. Same raise_b-before-ok_b block-ordering
    rule as _emit_list_index_bounds_check's own docstring explains."""
    neg_one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", neg_one, [-1]))
    is_miss = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_miss, [idx_v, neg_one]))

    raise_b = ctx.new_block(f"stridx_raise_{tag}")
    ok_b = ctx.new_block(f"stridx_ok_{tag}")
    ctx.emit(IRInstr("br.t", None, [is_miss, raise_b.label, ok_b.label]))

    ctx.switch_to(raise_b)
    msg_name = ctx.mctx.intern_str("substring not found")
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", msg_v, [msg_name]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["ValueError"]]))
    ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_v]))
    ctx.emit(IRInstr("br", None, [ok_b.label]))

    ctx.switch_to(ok_b)


def _emit_dict_key_check(ctx: _FuncCtx, dict_v: IRValue, key_v: IRValue, tag: int) -> None:
    """Raise KeyError(repr(key)) if key_v isn't present in dict_v -- only
    wired into the plain `d[key]` READ subscript site (like
    `_emit_list_index_bounds_check`'s matching note, `_abi_dict_get_default`
    itself stays unchecked since dict.get()/other internal helpers rely on
    its "return the default silently" behavior). CPython's real KeyError
    message is the key's repr in parens (e.g. `'missing'` for a str key);
    this backend's exception machinery only carries a plain message
    string today (see _abi_raise's signature), so a str key's repr is
    approximated inline rather than reusing the general repr formatter
    (avoids a circular dependency: the repr formatter is defined much
    later in this file and isn't needed for the CAUGHT `except KeyError:`
    case to work, only for whatever the user's handler chooses to print,
    which is a separate, already-correct feature)."""
    has_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", dict_v, key_v]))

    raise_b = ctx.new_block(f"keyerr_raise_{tag}")
    ok_b = ctx.new_block(f"keyerr_ok_{tag}")
    ctx.emit(IRInstr("br.t", None, [has_v, ok_b.label, raise_b.label]))

    ctx.switch_to(raise_b)
    # Real Python's KeyError message (str(e)) is the missing key's OWN
    # repr, quoted (e.g. `'missing'`) -- key_v is always a real string
    # pointer here (int keys are pre-stringified by _lower_dict_key for
    # the lookup itself), so build the quoted form directly rather than
    # leaving a bare "KeyError" placeholder.
    quote_name = ctx.mctx.intern_str("'")
    quote_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", quote_v, [quote_name]))
    opened_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", opened_v, ["_abi_str_concat", quote_v, key_v]))
    msg_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", msg_v, ["_abi_str_concat", opened_v, quote_v]))
    exc_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", exc_v, [BUILTIN_EXC_IDS["KeyError"]]))
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


def _lower_set_pairop(ctx: _FuncCtx, obj_e: A.Expr, other_e: A.Expr, method: str, tag: int) -> IRValue:
    """The set operations that TEST or MUTATE in place, rather than building a
    fresh set the way `_lower_set_setop` does:

      isdisjoint(o)           -> no key of self is in o
      issuperset(o)           -> every key of o is in self
      intersection_update(o)  -> drop keys of self that aren't in o
      difference_update(o)    -> drop keys of o from self

    All four are one walk over a key snapshot with a membership test against
    the other side, so they share this loop. `_abi_dict_keys` returns a fresh
    list, so popping from the set while walking that snapshot is safe (sets
    are dict-backed, hence the dict helpers).
    """
    obj_v = _lower_expr(ctx, obj_e)
    other_v = _lower_expr(ctx, other_e)
    # isdisjoint/intersection_update ask about SELF's keys; the other two ask
    # about the argument's keys.
    scan_self = method in ("isdisjoint", "intersection_update")
    scan_v = obj_v if scan_self else other_v
    test_v = other_v if scan_self else obj_v
    is_pred = method in ("isdisjoint", "issuperset")

    keys_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", scan_v]))
    res_ptr = ctx.ensure_slot(f"__spair_res_{tag}", I64)
    idx_ptr = ctx.ensure_slot(f"__spair_idx_{tag}", I64)
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    ctx.emit(IRInstr("store", None, [one_v, res_ptr]))
    z0_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z0_v, [0]))
    ctx.emit(IRInstr("store", None, [z0_v, idx_ptr]))
    klen_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", klen_addr, [keys_v, _LIST_LEN_OFF]))
    klen_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", klen_v, [klen_addr]))

    h_b = ctx.new_block("spairhead")
    b_b = ctx.new_block("spairbody")
    act_b = ctx.new_block("spairact")
    c_b = ctx.new_block("spaircont")
    e_b = ctx.new_block("spairend")
    ctx.emit(IRInstr("br", None, [h_b.label]))

    ctx.switch_to(h_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    go_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", go_v, [i_v, klen_v]))
    ctx.emit(IRInstr("br.t", None, [go_v, b_b.label, e_b.label]))

    ctx.switch_to(b_b)
    bi_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi_v, [idx_ptr]))
    kaddr = _list_elem_addr(ctx, keys_v, bi_v)
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", key_v, [kaddr]))
    if method == "difference_update":
        # Unconditional: every key of the argument leaves self.
        ctx.emit(IRInstr("br", None, [act_b.label]))
    else:
        has_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", test_v, key_v]))
        zc_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zc_v, [0]))
        hit_v = ctx.tmp(I64)
        if method in ("isdisjoint",):
            # act when the key IS shared (disjointness violated)
            ctx.emit(IRInstr("icmp.ne", hit_v, [has_v, zc_v]))
        else:
            # issuperset: act when a key is MISSING; intersection_update: drop
            # the keys that are missing from the other side.
            ctx.emit(IRInstr("icmp.eq", hit_v, [has_v, zc_v]))
        ctx.emit(IRInstr("br.t", None, [hit_v, act_b.label, c_b.label]))

    ctx.switch_to(act_b)
    if is_pred:
        pz_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", pz_v, [0]))
        ctx.emit(IRInstr("store", None, [pz_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [e_b.label]))  # short-circuit
    else:
        ctx.emit(IRInstr("call", None, ["_abi_dict_pop", obj_v, key_v]))
        ctx.emit(IRInstr("br", None, [c_b.label]))

    ctx.switch_to(c_b)
    ci_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", ci_v, [idx_ptr]))
    s1_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", s1_v, [1]))
    ni_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", ni_v, [ci_v, s1_v]))
    ctx.emit(IRInstr("store", None, [ni_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [h_b.label]))

    ctx.switch_to(e_b)
    out_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out_v, [res_ptr]))
    return out_v


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


def _lower_dict_eq(
    ctx: _FuncCtx, lhs_e: A.Expr, rhs_e: A.Expr, val_kind: str, tag: int
) -> IRValue:
    """1 if two dicts are equal BY VALUE (same size, same keys, equal values).

    Walks the left dict's ordered key list, requiring each key to be present in
    the right dict with an equal value; combined with the length check that is
    exactly CPython's mapping equality. Same shape as `_lower_set_subset`,
    which walks keys the same way -- dicts and sets share the layout.
    """
    res_ptr = ctx.ensure_slot(f"__deq_{tag}", I64)
    z0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z0, [0]))
    ctx.emit(IRInstr("store", None, [z0, res_ptr]))

    lhs_v = _lower_expr(ctx, lhs_e)
    rhs_v = _lower_expr(ctx, rhs_e)
    la = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", la, [lhs_v, _LIST_LEN_OFF]))
    llen = ctx.tmp(I64)
    ctx.emit(IRInstr("load", llen, [la]))
    ra = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", ra, [rhs_v, _LIST_LEN_OFF]))
    rlen = ctx.tmp(I64)
    ctx.emit(IRInstr("load", rlen, [ra]))

    keys_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", lhs_v]))
    kbuf_a = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", kbuf_a, [keys_v, _LIST_BUF_OFF]))
    kbuf = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", kbuf, [kbuf_a]))
    klen_a = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", klen_a, [keys_v, _LIST_LEN_OFF]))
    klen = ctx.tmp(I64)
    ctx.emit(IRInstr("load", klen, [klen_a]))

    idx_ptr = ctx.ensure_slot(f"__deqi_{tag}", I64)
    z1 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z1, [0]))
    ctx.emit(IRInstr("store", None, [z1, idx_ptr]))

    head_b = ctx.new_block(f"deqhead{tag}")
    body_b = ctx.new_block(f"deqbody{tag}")
    cmp_b = ctx.new_block(f"deqcmp{tag}")
    next_b = ctx.new_block(f"deqnext{tag}")
    same_b = ctx.new_block(f"deqsame{tag}")
    end_b = ctx.new_block(f"deqend{tag}")

    len_same = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", len_same, [llen, rlen]))
    ctx.emit(IRInstr("br.t", None, [len_same, head_b.label, end_b.label]))

    ctx.switch_to(head_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    more = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", more, [i_v, klen]))
    ctx.emit(IRInstr("br.t", None, [more, body_b.label, same_b.label]))

    ctx.switch_to(body_b)
    bi = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi, [idx_ptr]))
    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [8]))
    off = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", off, [bi, eight]))
    k_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", k_addr, [kbuf, off]))
    k_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", k_v, [k_addr]))
    has_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", rhs_v, k_v]))
    ctx.emit(IRInstr("br.t", None, [has_v, cmp_b.label, end_b.label]))

    ctx.switch_to(cmp_b)
    dflt = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", dflt, [0]))
    lval = ctx.tmp(I64)
    ctx.emit(IRInstr("call", lval, ["_abi_dict_get_default", lhs_v, k_v, dflt]))
    dflt2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", dflt2, [0]))
    rval = ctx.tmp(I64)
    ctx.emit(IRInstr("call", rval, ["_abi_dict_get_default", rhs_v, k_v, dflt2]))
    veq = ctx.tmp(I64)
    if val_kind == "str":
        ctx.emit(IRInstr("call", veq, ["_abi_str_eq", lval, rval]))
    elif val_kind == "float":
        lf = ctx.tmp(F64)
        ctx.emit(IRInstr("bitcast_i2f", lf, [lval]))
        rf = ctx.tmp(F64)
        ctx.emit(IRInstr("bitcast_i2f", rf, [rval]))
        ctx.emit(IRInstr("fcmp.eq", veq, [lf, rf]))
    else:
        ctx.emit(IRInstr("icmp.eq", veq, [lval, rval]))
    ctx.emit(IRInstr("br.t", None, [veq, next_b.label, end_b.label]))

    ctx.switch_to(next_b)
    ni = ctx.tmp(I64)
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    ctx.emit(IRInstr("iadd", ni, [bi, one]))
    ctx.emit(IRInstr("store", None, [ni, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(same_b)
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    ctx.emit(IRInstr("store", None, [one2, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out, [res_ptr]))
    return out


def _repr_el_kind(e) -> str:
    """The static element kind of a list/tuple expression, as the equality and
    repr paths need it: a tuple's slots are per-position, so a tuple only has a
    single element kind when every slot agrees."""
    _t = A.expr_type(e)
    if _t == "tuple":
        _slots = A.tuple_element_types(e)
        if not _slots:
            return "any"
        for _k in _slots:
            if _k != _slots[0]:
                return "mixed"
        return _slots[0]
    if isinstance(e, A.ListLit):
        return e.el_type or "int"
    return getattr(e, "list_el_type", "int") or "int"


def _lower_sequence_eq(
    ctx: _FuncCtx, lhs_e: A.Expr, rhs_e: A.Expr, el_kind: str, tag: int
) -> IRValue:
    """1 if two lists/tuples are equal BY VALUE (same length, elementwise
    equal), else 0.

    Python container equality is structural, but `==` on two list/tuple
    operands fell through to the chained-comparison path, which compares the
    two operands' raw POINTERS -- so `[1, 2] == [1, 2]` was False and
    `sorted(a) == sorted(b)` (the standard anagram test) could never be True.
    Short-circuits on the first unequal element.

    Elements compare by their static kind: str through `_abi_str_eq`, float
    through `fcmp.eq` (so 0.0 == -0.0 holds and NaN != NaN, as in CPython --
    a raw bit compare gets both backwards), everything else as a plain
    integer/pointer word.
    """
    return _emit_sequence_eq_value(
        ctx,
        _lower_expr(ctx, lhs_e),
        _lower_expr(ctx, rhs_e),
        el_kind,
        _inner_el_kind(lhs_e),
        tag,
    )


def _inner_el_kind(e) -> str:
    """One level further in: the element kind of a list/tuple's own elements,
    when those elements are themselves sequences. "any" when unknown."""
    _iv = getattr(e, "list_el_value_type", None)
    if isinstance(_iv, str) and _iv:
        return _iv
    return "any"


def _emit_sequence_eq_value(
    ctx: _FuncCtx,
    lhs_v: IRValue,
    rhs_v: IRValue,
    el_kind: str,
    inner_kind: str,
    tag: int,
) -> IRValue:
    """`_lower_sequence_eq`'s body, driven by two already-lowered sequence
    VALUES -- which is what lets it recurse: a list of lists compares each
    element pair with this same routine one level down, instead of comparing
    the two element POINTERS. Nesting depth is bounded by the static type, so
    the recursion always terminates.
    """
    res_ptr = ctx.ensure_slot(f"__seqeq_{tag}", I64)
    zero_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero_v, [0]))
    ctx.emit(IRInstr("store", None, [zero_v, res_ptr]))

    llen_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", llen_addr, [lhs_v, _LIST_LEN_OFF]))
    llen_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", llen_v, [llen_addr]))
    rlen_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", rlen_addr, [rhs_v, _LIST_LEN_OFF]))
    rlen_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", rlen_v, [rlen_addr]))
    lbuf_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", lbuf_addr, [lhs_v, _LIST_BUF_OFF]))
    lbuf_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", lbuf_v, [lbuf_addr]))
    rbuf_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", rbuf_addr, [rhs_v, _LIST_BUF_OFF]))
    rbuf_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", rbuf_v, [rbuf_addr]))

    idx_ptr = ctx.ensure_slot(f"__seqeqi_{tag}", I64)
    z2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z2, [0]))
    ctx.emit(IRInstr("store", None, [z2, idx_ptr]))

    head_b = ctx.new_block(f"seqeqhead{tag}")
    body_b = ctx.new_block(f"seqeqbody{tag}")
    next_b = ctx.new_block(f"seqeqnext{tag}")
    same_b = ctx.new_block(f"seqeqsame{tag}")
    end_b = ctx.new_block(f"seqeqend{tag}")

    len_same = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", len_same, [llen_v, rlen_v]))
    ctx.emit(IRInstr("br.t", None, [len_same, head_b.label, end_b.label]))

    ctx.switch_to(head_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [idx_ptr]))
    more_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", more_v, [i_v, llen_v]))
    ctx.emit(IRInstr("br.t", None, [more_v, body_b.label, same_b.label]))

    ctx.switch_to(body_b)
    bi_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", bi_v, [idx_ptr]))
    eight_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight_v, [8]))
    off_v = ctx.tmp(I64)
    ctx.emit(IRInstr("imul", off_v, [bi_v, eight_v]))
    la_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", la_addr, [lbuf_v, off_v]))
    ra_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", ra_addr, [rbuf_v, off_v]))
    if el_kind == "float":
        la_v = ctx.tmp(F64)
        ctx.emit(IRInstr("load", la_v, [la_addr]))
        ra_v = ctx.tmp(F64)
        ctx.emit(IRInstr("load", ra_v, [ra_addr]))
        eq_v = ctx.tmp(I64)
        ctx.emit(IRInstr("fcmp.eq", eq_v, [la_v, ra_v]))
    elif el_kind in ("list", "tuple"):
        # A NESTED container element compares by value too, one level down.
        # Depth is bounded by the static type, so the recursion terminates.
        la_p = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", la_p, [la_addr]))
        ra_p = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", ra_p, [ra_addr]))
        eq_v = _emit_sequence_eq_value(
            ctx, la_p, ra_p, inner_kind, "any", tag * 31 + 7
        )
    else:
        la_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", la_v, [la_addr]))
        ra_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", ra_v, [ra_addr]))
        eq_v = ctx.tmp(I64)
        if el_kind == "str":
            ctx.emit(IRInstr("call", eq_v, ["_abi_str_eq", la_v, ra_v]))
        else:
            ctx.emit(IRInstr("icmp.eq", eq_v, [la_v, ra_v]))
    ctx.emit(IRInstr("br.t", None, [eq_v, next_b.label, end_b.label]))

    ctx.switch_to(next_b)
    # RELOAD the index rather than reusing the value the body loaded. When the
    # element comparison is itself a loop (a list OF lists recurses here), a
    # value carried from the body block across that inner cycle is live across
    # a back-edge that did not exist when it was defined -- the emitted program
    # then looped forever. Reloading keeps every live range inside one block.
    ni_prev = ctx.tmp(I64)
    ctx.emit(IRInstr("load", ni_prev, [idx_ptr]))
    ni_v = ctx.tmp(I64)
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    ctx.emit(IRInstr("iadd", ni_v, [ni_prev, one_v]))
    ctx.emit(IRInstr("store", None, [ni_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(same_b)
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    ctx.emit(IRInstr("store", None, [one2, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out_v, [res_ptr]))
    return out_v


def _lower_set_subset(ctx: _FuncCtx, sub_e: A.Expr, sup_e: A.Expr, tag: int) -> IRValue:
    """1 if every member of `sub_e` is also a member of `sup_e` (subset
    test underlying `<=`/`<`/`>=`/`>` on two sets), else 0. Ports
    codegen.py's _runtime_set_subset algorithm (and this file's own
    sibling _lower_set_setop's identical "walk a's ordered keys, check
    each against b via _abi_dict_contains" shape) as IR blocks: short-
    circuits false on the first member of `sub_e` not found in `sup_e`;
    the empty set is a subset of everything (including itself), so an
    empty `sub_e` (len 0) falls straight through the loop to "no miss
    found" -> 1.

    Was entirely unimplemented on this backend -- `<=`/`<`/`>=`/`>`
    between two sets fell through to the generic int-compare path,
    silently comparing the two sets' raw HEADER POINTER values instead
    of any subset logic (confirmed: `{"a"} <= {"a","b","c"}` produced
    False instead of True, and every other comparison in the same
    combined print() came out wrong too -- not a crash, a real
    correctness bug capable of flipping which branch downstream code
    takes)."""
    sub_v = _lower_expr(ctx, sub_e)
    sup_v = _lower_expr(ctx, sup_e)
    keys_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", sub_v]))
    idx_ptr = ctx.ensure_slot(f"__setsub_idx_{tag}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
    res_ptr = ctx.ensure_slot(f"__setsub_res_{tag}", I64)

    head_b = ctx.new_block("setsubhead")
    body_b = ctx.new_block("setsubbody")
    cont_b = ctx.new_block("setsubcont")
    miss_b = ctx.new_block("setsubmiss")
    hit_b = ctx.new_block("setsubhit")
    join_b = ctx.new_block("setsubjoin")

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
    ctx.emit(IRInstr("br.t", None, [cond, body_b.label, hit_b.label]))

    ctx.switch_to(body_b)
    key_addr = _list_elem_addr(ctx, keys_v, idx_v)
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", key_v, [key_addr]))
    has_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", sup_v, key_v]))
    ctx.emit(IRInstr("br.t", None, [has_v, cont_b.label, miss_b.label]))

    ctx.switch_to(cont_b)
    one_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_v, [1]))
    next_v = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_v, [idx_v, one_v]))
    ctx.emit(IRInstr("store", None, [next_v, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(miss_b)
    zero2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero2, [0]))
    ctx.emit(IRInstr("store", None, [zero2, res_ptr]))
    ctx.emit(IRInstr("br", None, [join_b.label]))

    ctx.switch_to(hit_b)
    one2 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one2, [1]))
    ctx.emit(IRInstr("store", None, [one2, res_ptr]))
    ctx.emit(IRInstr("br", None, [join_b.label]))

    ctx.switch_to(join_b)
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out, [res_ptr]))
    return out


_NARROW_PRIM_NAMES = ("bool", "int", "float", "str", "list", "dict", "tuple", "set")


def _narrowed_if_type(ctx: _FuncCtx, test: A.Expr) -> tuple[str, str] | None:
    """Recognize `if type(x) is T:` / `if isinstance(x, T):` (plain bare
    name `x`, single concrete target `T`) and return `(x, T)` so `A.If`'s
    lowering can narrow `x` to concrete type `T` for the "then" body only.

    Only fires when `x`'s STATIC type is genuinely "any" -- narrowing a
    variable that's already concretely typed has nothing to do (its reads
    already return the right thing), and narrowing would be actively wrong
    for a variable sema already knows isn't "any" (its slot never holds
    one of `_lower_box_any`'s tagged cells to unbox in the first place).
    Returns None for every other shape (chained comparisons, `isinstance`
    with a tuple of targets, a non-bare-name receiver, an already-concrete
    receiver, ...) -- the caller's narrowing is simply skipped, which is
    always safe (merely means the checked-any payload stays boxed/
    unreadable inside that branch, the pre-existing state of the world
    before this function existed).
    """
    if (
        isinstance(test, A.Compare)
        and len(test.ops) == 1
        and test.ops[0] == "is"
        and isinstance(test.operands[0], A.Call)
        and test.operands[0].func == "type"
        and len(test.operands[0].args) == 1
        and isinstance(test.operands[0].args[0], A.Name)
        and isinstance(test.operands[1], A.Name)
    ):
        name_node = test.operands[0].args[0]
        target = test.operands[1]
        if A.expr_type(name_node) != "any":
            return None
        if target.name in ctx.mctx.class_ids:
            return name_node.name, f"instance:{target.name}"
        if target.name in _NARROW_PRIM_NAMES:
            return name_node.name, target.name
        return None
    if (
        isinstance(test, A.Call)
        and test.func == "isinstance"
        and len(test.args) == 2
        and isinstance(test.args[0], A.Name)
    ):
        name_node = test.args[0]
        if A.expr_type(name_node) != "any":
            return None
        target = test.args[1]
        if isinstance(target, A.Name):
            if target.name in ctx.mctx.class_ids:
                return name_node.name, f"instance:{target.name}"
            if target.name in _NARROW_PRIM_NAMES:
                return name_node.name, target.name
        return None
    return None


def _lower_narrowed_name_read(ctx: _FuncCtx, name: str) -> IRValue:
    """Read a variable currently narrowed to a concrete type (see
    `_narrowed_if_type`/`A.If`'s lowering), unboxing its (possibly still
    boxed -- see `_lower_box_any`) value exactly once per branch and
    caching the result so every subsequent read in the same branch reuses
    it instead of unboxing again (unsafe -- see `_lower_expr`'s
    docstring)."""
    cached = ctx.narrowed_cache.get(name)
    if cached is not None:
        return cached
    ty = ctx.slot_ty.get(name, ctx.mctx.global_types.get(name, PTR))
    ptr = _name_value_ptr(ctx, name, ty)
    raw = ctx.tmp(ty)
    ctx.emit(IRInstr("load", raw, [ptr]))
    unboxed = _lower_unbox_any(ctx, raw)
    if ctx.narrowed_types.get(name) == "float":
        # `_lower_unbox_any` always returns a PTR-typed IRValue (it has no
        # way to know from the boxed cell alone whether the caller wants
        # the raw bit pattern reinterpreted as a pointer or a float) --
        # its bits ARE the right float bit pattern (boxed by
        # `_lower_box_any` via `bitcast_f2i`), just carrying the wrong IR
        # type tag, which would put it in a general-purpose register
        # instead of an XMM one downstream. Round-trip through a fresh
        # stack slot to reinterpret (not convert) the bits as F64 --
        # relabeling the SAME SSA value in place (mirrors the sext-relabel
        # pattern used elsewhere in this file for an identical "right
        # bits, wrong declared IRType" situation) made the register
        # allocator crash (`RegLoc has no attribute 'offset'`) once the
        # value was cached and reused across statements; a real
        # store/load pair is the safe way to reinterpret bits across a
        # register-class boundary that survives caching. An actual
        # `bitcast_i2f` would be WRONG here -- it numerically converts the
        # pointer's bit pattern into a numerically-equal double instead of
        # reinterpreting the same bits.
        reinterp_ptr = ctx.ensure_slot(f"__anyunbox_f64_{name}", F64)
        int_view_ptr = IRValue(reinterp_ptr.name, PTR)
        ctx.emit(IRInstr("store", None, [unboxed, int_view_ptr]))
        fv = ctx.tmp(F64)
        ctx.emit(IRInstr("load", fv, [reinterp_ptr]))
        unboxed = fv
    ctx.narrowed_cache[name] = unboxed
    return unboxed


def _lower_is_bool_literal(ctx: _FuncCtx, e: A.Compare) -> IRValue | None:
    """`x is True` / `x is False` (either operand order) where the other side
    is opaque. Returns None when the comparison is not that shape.

    In CPython `1 is True` is False: `True` is a distinct object from the
    integer 1. asmpython has no separate bool type -- bool IS int everywhere
    (see `A.is_bool_expr`'s docstring) -- so once the read choke unboxes an
    "any" operand, a boxed `1` and a boxed `True` are the same integer and
    plain identity cannot separate them.

    The BOX still can: `_lower_box_any` tags them bool(-4) and int(-1)
    respectively. So compare the runtime TAG as well as the payload, reading
    it from the still-boxed cell (`_lower_expr_inner`) before the unbox
    happens. A never-boxed operand reports UNTAGGED and correctly answers
    False.

    This is what made `json.dumps({"a": 1})` render 1 as `true`: stdlib
    json's encoder tests `if obj is True` first, exactly as CPython's does,
    and every integer 1 matched it.
    """
    lit = None
    other = None
    for a, b in ((e.operands[0], e.operands[1]), (e.operands[1], e.operands[0])):
        if isinstance(a, A.IntLit) and getattr(a, "is_bool", False):
            lit, other = a, b
            break
    if lit is None or other is None:
        return None
    if A.expr_type(other) != "any":
        # A concretely-typed operand carries no box to read a tag from, and
        # `x is True` on a real int is already answered by the ordinary
        # identity compare below.
        return None

    raw = _lower_expr_inner(ctx, other)
    tag_v = _lower_read_any_tag(ctx, raw)
    payload = _lower_unbox_any(ctx, raw)

    bool_tag = ctx.tmp(I64)
    ctx.emit(IRInstr("const", bool_tag, [BUILTIN_TYPE_IDS["bool"]]))
    is_bool_tagged = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_bool_tagged, [tag_v, bool_tag]))

    want = ctx.tmp(I64)
    ctx.emit(IRInstr("const", want, [1 if lit.value else 0]))
    same_value = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", same_value, [payload, want]))

    result = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", result, [is_bool_tagged, same_value]))
    if e.ops[0] == "is not":
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        inv = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", inv, [result, zero]))
        return inv
    return result


def _lower_type_is_compare(ctx: _FuncCtx, type_call: A.Call, target: A.Expr) -> IRValue | None:
    """`type(x) is T` / `type(x) is not T` -- idiomatic Python's usual
    spelling of a concrete-type check (portapy's own `_full_box`, the
    motivating real-world case for this whole feature, is written this
    way throughout, not with `isinstance`).

    This is NOT the same AST shape `isinstance()` gets: `type(x) is T` is
    a plain `A.Compare`, and without this special case it falls through to
    the generic chained-comparison path far below, which lowers `type(x)`
    (now correctly, via the runtime tag dispatch added elsewhere in this
    file) and `T` (a bare class/builtin-type name, which loads its
    RTTI/BUILTIN_TYPE_IDS id -- an int) SEPARATELY and then compares a
    STRING POINTER against a small int, which is never true. Confirmed via
    a repro: `type(value) is int` inside a function always took the "not
    int" branch even for a genuine int argument.

    Only the two operand shapes `type(x) is <bare name>` / `<bare name> is
    type(x)` are recognized (`target` must resolve to a real class or
    BUILTIN_TYPE_IDS name) -- anything else (e.g. `type(x) is type(y)`)
    returns None so the caller falls through to the ordinary identity-
    compare path, unchanged from today's (already broken, out of scope)
    behavior for that shape.
    """
    if not (isinstance(target, A.Name) and (target.name in BUILTIN_TYPE_IDS or target.name in ctx.mctx.class_ids)):
        return None
    arg = type_call.args[0]
    arg_t = A.expr_type(arg)
    if target.name in ctx.mctx.class_ids:
        # `type(x) is SomeClass` -- exact match only (unlike isinstance,
        # `type()` never matches a subclass), so compare against exactly
        # this one class id, not the whole _subclass_ids accept-list.
        if arg_t.startswith("instance:") or arg_t == "any":
            obj_v = _lower_expr_inner(ctx, arg)
            zero = ctx.tmp(PTR)
            ctx.emit(IRInstr("const", zero, [0]))
            out_ptr = ctx.ensure_slot(f"__typeis_out_{id(type_call)}", I64)
            none_b = ctx.new_block("typeisnone")
            live_b = ctx.new_block("typeislive")
            end_b = ctx.new_block("typeisend")
            is_none = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", is_none, [obj_v, zero]))
            ctx.emit(IRInstr("br.t", None, [is_none, none_b.label, live_b.label]))
            ctx.switch_to(none_b)
            false_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", false_v, [0]))
            ctx.emit(IRInstr("store", None, [false_v, out_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(live_b)
            tag_v = _lower_read_any_tag(ctx, obj_v)
            cid_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", cid_v, [ctx.mctx.class_ids[target.name]]))
            match_v = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", match_v, [tag_v, cid_v]))
            ctx.emit(IRInstr("store", None, [match_v, out_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(end_b)
            out = ctx.tmp(I64)
            ctx.emit(IRInstr("load", out, [out_ptr]))
            return out
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("const", out, [1 if arg_t == f"instance:{target.name}" else 0]))
        return out
    prim_tags = {
        "bool": BUILTIN_TYPE_IDS["bool"],
        "int": BUILTIN_TYPE_IDS["int"],
        "float": BUILTIN_TYPE_IDS["float"],
        "str": BUILTIN_TYPE_IDS["str"],
        "list": BUILTIN_TYPE_IDS["list"],
        "dict": BUILTIN_TYPE_IDS["dict"],
        "tuple": BUILTIN_TYPE_IDS["tuple"],
        "set": BUILTIN_TYPE_IDS["set"],
    }
    if arg_t != "any":
        _lower_expr(ctx, arg)
        if target.name == "bool":
            match = arg_t == "int" and A.is_bool_expr(arg)
        elif target.name == "int":
            match = arg_t == "int" and not A.is_bool_expr(arg) and not A.is_none_expr(arg)
        else:
            match = arg_t == target.name
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("const", out, [1 if match else 0]))
        return out
    # `type(x) is <primitive>` where x's STATIC type is "any": read the
    # runtime tag (see `_lower_read_any_tag`) and compare against exactly
    # this one BUILTIN_TYPE_IDS entry (`type()` is always an exact match,
    # never a subclass match).
    obj_v = _lower_expr_inner(ctx, arg)
    tag_v = _lower_read_any_tag(ctx, obj_v)
    want_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", want_v, [prim_tags[target.name]]))
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", out, [tag_v, want_v]))
    return out


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
    if has_prim_target and arg0_t != "any":
        _lower_expr(ctx, arg0)
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("const", out, [1 if prim_match else 0]))
        return out

    if has_prim_target:
        # `isinstance(x, int)` (etc.) where x's STATIC type is "any" --
        # unlike the branch above, the answer genuinely isn't known at
        # compile time (x could be any boxed scalar kind at runtime). Read
        # the runtime tag `_lower_box_any` stamped on it (see that
        # function's docstring) and compare against the SAME primitive
        # targets' BUILTIN_TYPE_IDS ids, the scalar-kind mirror of the
        # class-target branch below (which already does this for
        # instances via `_subclass_ids`/class_ids). Uses
        # `_lower_expr_inner`, not `_lower_expr`, so it sees the raw
        # (possibly still-boxed) cell instead of the wrapper's
        # auto-unboxed value.
        accept_tags: list[int] = []
        for t in targets:
            if t == "bool":
                accept_tags.append(BUILTIN_TYPE_IDS["bool"])
            elif t == "int":
                accept_tags.append(BUILTIN_TYPE_IDS["int"])
            elif t in BUILTIN_TYPE_IDS:
                accept_tags.append(BUILTIN_TYPE_IDS[t])
        obj_v = _lower_expr_inner(ctx, arg0)
        tag_v = _lower_read_any_tag(ctx, obj_v)
        match_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", match_v, [0]))
        for tag in accept_tags:
            tag_const = ctx.tmp(I64)
            ctx.emit(IRInstr("const", tag_const, [tag]))
            eq_v = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", eq_v, [tag_v, tag_const]))
            next_v = ctx.tmp(I64)
            ctx.emit(IRInstr("ior", next_v, [match_v, eq_v]))
            match_v = next_v
        return match_v

    # `isinstance(x, object)` is True for every value in Python (object is
    # the universal base). Answer it directly -- object is not a user class,
    # so it would otherwise fall through to the instance dict-probe below and
    # both mis-answer (empty `accept` -> always False) and, on a non-dict
    # value, dereference a non-dict cell and fault.
    if "object" in targets:
        _lower_expr(ctx, arg0)
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("const", out, [1]))
        return out

    # `isinstance(x, type)` asks whether x is itself a class object. asmpython
    # represents a class value as its small non-negative class-id integer,
    # which is ambiguous with an ordinary int at the value level, so there is
    # no reliable runtime discriminator; but the dominant use is a guard like
    # `isinstance(err, type) and issubclass(err, ...)` where `err` is an
    # ordinary value (an exception INSTANCE, an int, a str) -- never a bare
    # class. Answer conservatively False via the fault-safe tag reader (so a
    # boxed/opaque value never reaches the crashing dict-probe below), which
    # correctly skips the issubclass branch for every non-class value. `type`
    # is not a user class, so leaving it in `targets` would otherwise fall
    # into that dict-probe and fault on a boxed-int/raw value.
    if "type" in targets and not any(t in ctx.mctx.class_ids for t in targets):
        # Evaluate the tag for its fault-safety/side effects, then yield 0.
        obj_v = _lower_expr_inner(ctx, arg0)
        _lower_read_any_tag(ctx, obj_v)
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("const", out, [0]))
        return out

    accept: list[int] = []
    for t in targets:
        # A descriptor-wrapper target (`isinstance(v, staticmethod)` /
        # classmethod / property): its tag lives in BUILTIN_TYPE_IDS, stored
        # in the wrapper cell's "__class__" key by _lower_descriptor_wrapper,
        # read the same way a user class id is a few lines below. Add that
        # tag directly (it isn't a user class, so _subclass_ids finds
        # nothing for it).
        if t in _DESCRIPTOR_WRAPPERS and BUILTIN_TYPE_IDS[t] not in accept:
            accept.append(BUILTIN_TYPE_IDS[t])
        for cid in _subclass_ids(ctx, t):
            if cid not in accept:
                accept.append(cid)

    obj_v = _lower_expr_inner(ctx, arg0)
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
    # Read the runtime class id via the FAULT-SAFE tag reader, not a direct
    # `_abi_dict_get_default(obj, "__class__", -1)` dict-probe. `obj_v` here is
    # the raw (possibly BOXED) value of an `any` argument: `isinstance(x,
    # UserClass)` where x is a boxed scalar (a `dict[str,object]` value, an
    # `object` field/param) would otherwise dereference the box cell as a dict
    # -- reading its payload word as a slot-buffer pointer and faulting.
    # `_lower_read_any_tag` returns a scalar box's BUILTIN_TYPE_IDS tag (never
    # a user class id, so a boxed int correctly fails the class check) for a
    # box, the real class id for an instance dict, and UNTAGGED for a raw
    # container -- all without ever dereferencing a non-instance.
    class_id = _lower_read_any_tag(ctx, obj_v)
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


def _lower_sort_key_call(ctx: _FuncCtx, sort_key: A.Lambda, el_kind: str, item_v: IRValue) -> IRValue | None:
    """Compute a `key=` lambda's result for one element, shared by
    `_lower_sort_inplace` and `_lower_minmax`. Returns None if the
    lambda body isn't one of the supported shapes (caller decides what
    to do -- currently always `raise LowerError`, kept at each call site
    rather than centralized here so the error message stays specific to
    the caller).

    `lambda w: len(w)` over str elements gets a dedicated fast path
    (direct `strlen` call) rather than routing through the general
    synthesized-function call. Root cause (confirmed via a background
    gdb investigation): sema.py's Lambda type-checking seeds EVERY
    lambda parameter as `"any"` regardless of the actual call-site
    element type (`inner_scope.add(p, "any")`, unconditional) -- so
    inside the synthesized function body, `len(w)` type-checks `w` as
    `"any"`, and `ir_lower.py`'s `len()` lowering branches on that
    static type: `str` calls `strlen`, but `any`/`dict`/`set` reads a
    LIST-style length header field via `gep [ptr+8]; load` instead. A
    real (unheadered) Python str has no such field -- that `load`
    silently reinterprets the string's OWN character bytes 8-15 as a
    64-bit length integer, producing a per-string "key" that's actually
    garbage bytes-as-an-int, scrambling comparisons. This is a real,
    general sema gap (also affects `map()`/`filter()` over str calling
    `len()` inside their lambda), not something safe to fix narrowly
    here beyond this one shape -- call `strlen` directly instead,
    bypassing the mistyped general path for exactly this common case.
    General non-fast-path str-element lambda bodies still aren't
    supported (returns None); int-element lambda bodies of any shape
    ARE supported via the general synthesized-function-call path,
    confirmed correct via direct testing.
    """
    param = sort_key.params[0]
    key_body = sort_key.body
    if isinstance(key_body, A.Name) and key_body.name == param:
        return item_v
    if (
        isinstance(key_body, A.Subscript)
        and isinstance(key_body.obj, A.Name)
        and key_body.obj.name == param
        and isinstance(key_body.index, A.IntLit)
        # This reads slot N of the element's list/tuple buffer, so it only
        # applies to list/tuple-shaped elements. A STR element's `s[1]` is a
        # character read, not a buffer slot -- taking this path on a real str
        # dereferenced its bytes as a header and crashed. Let it fall through
        # to the general synthesized-function call, which handles str
        # correctly now that the lambda's parameter is typed.
        and el_kind != "str"
    ):
        key_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("const", key_idx, [int(key_body.index.value)]))
        key_addr = _list_elem_addr(ctx, item_v, key_idx)
        key_v = ctx.tmp(ir_type_for(A.expr_type(key_body)))
        ctx.emit(IRInstr("load", key_v, [key_addr]))
        return key_v
    if (
        el_kind == "str"
        and isinstance(key_body, A.Call)
        and key_body.func == "len"
        and len(key_body.args) == 1
        and isinstance(key_body.args[0], A.Name)
        and key_body.args[0].name == param
    ):
        key_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", key_v, ["strlen", item_v]))
        return key_v
    if el_kind in ("int", "str", "dict", "tuple", "list") or el_kind.startswith("instance:"):
        # Call the lambda's own synthesized function with the element. Every
        # element kind except float routes through here, so an ARBITRARY body
        # works rather than only the three hard-coded fast shapes above --
        # `key=lambda s: s.lower()`, `key=lambda x: -x[1]`, `key=by_second`.
        # These kinds were previously excluded because sema typed every lambda
        # parameter "any", which mis-compiled the body (a `len(s)` on a real str
        # read a list header off it); the parameter is now typed from the
        # sequence's element kind, and for a tuple element from its per-slot
        # kinds too (`param_hint` / `param_tuple_types`).
        fn_name = sort_key.func_name  # type: ignore[attr-defined]
        key_ty = ir_type_for(getattr(sort_key, "lambda_ret", "int"))
        key_v = ctx.tmp(key_ty)
        ctx.emit(IRInstr("call", key_v, [fn_name, item_v]))
        return key_v
    return None


def _lower_sort_tuple_int_first(
    ctx: _FuncCtx, e, out_v: IRValue
) -> IRValue:
    """Sort tuple-layout elements by their first integer slot."""
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [out_v, _LIST_LEN_OFF]))
    length = ctx.tmp(I64)
    ctx.emit(IRInstr("load", length, [len_addr]))
    keys = _new_list_from_len(ctx, length)
    idx_ptr = ctx.ensure_slot(f"__sorttuple_idx_{id(e)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, idx_ptr]))

    head_b = ctx.new_block("sorttuplehead")
    body_b = ctx.new_block("sorttuplebody")
    cont_b = ctx.new_block("sorttuplecont")
    end_b = ctx.new_block("sorttupleend")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    index = ctx.tmp(I64)
    ctx.emit(IRInstr("load", index, [idx_ptr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [index, length]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    tuple_addr = _list_elem_addr(ctx, out_v, index)
    tuple_value = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", tuple_value, [tuple_addr]))
    first_addr = _list_elem_addr(ctx, tuple_value, zero)
    first = ctx.tmp(I64)
    ctx.emit(IRInstr("load", first, [first_addr]))
    ctx.emit(IRInstr("call", None, ["_abi_list_append", keys, first]))
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    current = ctx.tmp(I64)
    ctx.emit(IRInstr("load", current, [idx_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    following = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", following, [current, one]))
    ctx.emit(IRInstr("store", None, [following, idx_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(end_b)
    ctx.emit(IRInstr("call", None, ["_abi_sort_pairs_int", out_v, keys]))
    return out_v


def _lower_sort_inplace(
    ctx: _FuncCtx,
    e,
    out_v: IRValue,
    el_kind: str,
    tuple_key_kind: str = "str",
) -> IRValue:
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
        key_v = _lower_sort_key_call(ctx, sort_key, el_kind, item_v)
        if key_v is None:
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
        elif el_kind == "tuple" and tuple_key_kind == "int":
            out_v = _lower_sort_tuple_int_first(ctx, e, out_v)
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


def _minmax_is_better(ctx: _FuncCtx, cand: IRValue, best: IRValue, el_ty: str, want_max: bool) -> IRValue:
    """1 if `cand` should replace `best` (candidate is strictly greater for
    max, strictly less for min), matching Python's min/max first-wins tie
    behavior (only replace on a STRICT improvement)."""
    if el_ty == "str":
        cmp_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", cmp_v, ["_abi_str_cmp", cand, best]))
        zero = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero, [0]))
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.gt" if want_max else "icmp.lt", out, [cmp_v, zero]))
        return out
    if el_ty == "float":
        out = ctx.tmp(I64)
        ctx.emit(IRInstr("fcmp.gt" if want_max else "fcmp.lt", out, [cand, best]))
        return out
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.gt" if want_max else "icmp.lt", out, [cand, best]))
    return out


def _lower_minmax(ctx: _FuncCtx, e: A.Call) -> IRValue:
    """min(a, b, ...) / min(iterable[, key=...]) and max()'s mirror --
    entirely unimplemented before this: every shape fell through to the
    generic bare-symbol-call fallback, linking against a nonexistent
    `min`/`max` DLL symbol. sema.py already fully validates arity/kwargs
    and stamps `sort_key`/`inferred_type` (shared with sorted()'s own
    kwarg handling via `_check_sort_kwargs`) -- only the lowering itself
    was missing. Ports codegen.py's algorithm shapes (2-arg direct
    compare, 3+-arg running-best loop, 1-arg list/tuple scan with an
    optional key= callable) as IR blocks instead of inline asm.
    """
    want_max = e.func == "max"
    sort_key = getattr(e, "sort_key", None)

    if len(e.args) >= 2:
        # Variadic scalar form: running-best across N candidates. No
        # key= support here (sema already rejects that combination).
        el_ty = A.expr_type(e)
        first_v = _lower_expr(ctx, e.args[0])
        best_ty = ir_type_for(el_ty)
        best_ptr = ctx.ensure_slot(f"__minmax_best_{id(e)}", best_ty)
        ctx.emit(IRInstr("store", None, [first_v, best_ptr]))
        for arg in e.args[1:]:
            cand_v = _lower_expr(ctx, arg)
            best_v = ctx.tmp(best_ty)
            ctx.emit(IRInstr("load", best_v, [best_ptr]))
            better = _minmax_is_better(ctx, cand_v, best_v, el_ty, want_max)
            take_b = ctx.new_block("minmaxtake")
            skip_b = ctx.new_block("minmaxskip")
            cont_b = ctx.new_block("minmaxcont")
            ctx.emit(IRInstr("br.t", None, [better, take_b.label, skip_b.label]))
            ctx.switch_to(take_b)
            ctx.emit(IRInstr("store", None, [cand_v, best_ptr]))
            ctx.emit(IRInstr("br", None, [cont_b.label]))
            ctx.switch_to(skip_b)
            ctx.emit(IRInstr("br", None, [cont_b.label]))
            ctx.switch_to(cont_b)
        out = ctx.tmp(best_ty)
        ctx.emit(IRInstr("load", out, [best_ptr]))
        return out

    # 1-arg form: scan a list/tuple. Result element type is the SEQUENCE's
    # element kind (A.expr_type(e) mirrors it via sema's _list_el_type),
    # not necessarily what the key= callable returns -- the key is only
    # used to pick which element wins, never returned itself.
    src_v = _lower_expr(ctx, e.args[0])
    el_ty = A.expr_type(e)
    el_ir_ty = ir_type_for(el_ty)
    len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", len_addr, [src_v, _LIST_LEN_OFF]))
    len_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", len_v, [len_addr]))
    best_ptr = ctx.ensure_slot(f"__minmax1_best_{id(e)}", el_ir_ty)
    done_b = ctx.new_block("minmax1done")
    minmax_default = getattr(e, "minmax_default", None)
    if minmax_default is not None:
        # default= : an empty iterable yields the default rather than reading
        # element 0 (which would index one past the end). Branch on len == 0;
        # the non-empty path falls through to the normal first-element seed.
        zero_len = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_len, [0]))
        is_empty = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", is_empty, [len_v, zero_len]))
        empty_b = ctx.new_block("minmax1empty")
        nonempty_b = ctx.new_block("minmax1nonempty")
        ctx.emit(IRInstr("br.t", None, [is_empty, empty_b.label, nonempty_b.label]))
        ctx.switch_to(empty_b)
        def_v = _lower_expr(ctx, minmax_default)
        ctx.emit(IRInstr("store", None, [def_v, best_ptr]))
        ctx.emit(IRInstr("br", None, [done_b.label]))
        ctx.switch_to(nonempty_b)
    zero_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero_idx, [0]))
    first_addr = _list_elem_addr(ctx, src_v, zero_idx)
    first_el = ctx.tmp(el_ir_ty)
    ctx.emit(IRInstr("load", first_el, [first_addr]))
    ctx.emit(IRInstr("store", None, [first_el, best_ptr]))

    key_ty = "int"
    if isinstance(sort_key, A.Lambda):
        key_ty = getattr(sort_key, "lambda_ret", "int")
        best_key_ptr = ctx.ensure_slot(f"__minmax1_bestkey_{id(e)}", ir_type_for(key_ty))
        first_key = _lower_sort_key_call(ctx, sort_key, el_ty, first_el)
        if first_key is None:
            raise LowerError("unsupported expr Call (min/max key lambda body)")
        ctx.emit(IRInstr("store", None, [first_key, best_key_ptr]))

    idx_ptr = ctx.ensure_slot(f"__minmax1_idx_{id(e)}", I64)
    one_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_idx, [1]))
    ctx.emit(IRInstr("store", None, [one_idx, idx_ptr]))

    head_b = ctx.new_block("minmax1head")
    body_b = ctx.new_block("minmax1body")
    cont_b = ctx.new_block("minmax1cont")
    end_b = ctx.new_block("minmax1end")
    ctx.emit(IRInstr("br", None, [head_b.label]))

    ctx.switch_to(head_b)
    idx_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
    keep_going = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", keep_going, [idx_v, len_v]))
    ctx.emit(IRInstr("br.t", None, [keep_going, body_b.label, end_b.label]))

    ctx.switch_to(body_b)
    body_idx = ctx.tmp(I64)
    ctx.emit(IRInstr("load", body_idx, [idx_ptr]))
    cand_addr = _list_elem_addr(ctx, src_v, body_idx)
    cand_el = ctx.tmp(el_ir_ty)
    ctx.emit(IRInstr("load", cand_el, [cand_addr]))
    if isinstance(sort_key, A.Lambda):
        cand_key = _lower_sort_key_call(ctx, sort_key, el_ty, cand_el)
        if cand_key is None:
            raise LowerError("unsupported expr Call (min/max key lambda body)")
        best_key_v = ctx.tmp(ir_type_for(key_ty))
        ctx.emit(IRInstr("load", best_key_v, [best_key_ptr]))
        better = _minmax_is_better(ctx, cand_key, best_key_v, key_ty, want_max)
    else:
        best_el = ctx.tmp(el_ir_ty)
        ctx.emit(IRInstr("load", best_el, [best_ptr]))
        better = _minmax_is_better(ctx, cand_el, best_el, el_ty, want_max)
    take_b = ctx.new_block("minmax1take")
    ctx.emit(IRInstr("br.t", None, [better, take_b.label, cont_b.label]))
    ctx.switch_to(take_b)
    ctx.emit(IRInstr("store", None, [cand_el, best_ptr]))
    if isinstance(sort_key, A.Lambda):
        ctx.emit(IRInstr("store", None, [cand_key, best_key_ptr]))
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
    ctx.emit(IRInstr("br", None, [done_b.label]))
    # Both the loop exit (non-empty) and the empty-default path converge here.
    ctx.switch_to(done_b)
    out = ctx.tmp(el_ir_ty)
    ctx.emit(IRInstr("load", out, [best_ptr]))
    return out


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
    tuple_key_kind = (
        "str"
        if isinstance(arg, A.MethodCall) and arg.method == "items"
        else getattr(arg, "list_el_value_type", "int") or "int"
    )
    return _lower_sort_inplace(
        ctx, e, out_v, el_kind, tuple_key_kind=tuple_key_kind
    )


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


def _lower_expr_inner(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    if isinstance(e, A.IntLit):
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", v, [int(e.value)]))
        return v

    if isinstance(e, A.FloatLit):
        v = ctx.tmp(F64)
        ctx.emit(IRInstr("const", v, [float(e.value)]))
        return v

    if isinstance(e, A.Name):
        if e.name in ctx.narrowed_types:
            return _lower_narrowed_name_read(ctx, e.name)
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
        if e.name in ctx.mctx.ffi_consts and e.name not in ctx.slot_ty:
            # `from math import pi` -- a bare name bound to a stdlib.Const,
            # not a real runtime global at all (the binding table itself
            # IS the value, resolved at COMPILE time). Was entirely
            # unhandled: fell through to the generic slot/global fallback
            # below, which allocated a fresh, never-initialized local
            # slot defaulting to I64 and read GARBAGE stack memory as the
            # constant's value -- confirmed via a real repro (`from math
            # import pi, sqrt; print(int(sqrt(pi * pi)))`) crashing the
            # COMPILER itself (not the compiled binary): the garbage
            # I64-typed value flowed into `pi * pi`'s `fmul`, and
            # regalloc allocated it a GP register to match its wrong
            # type, so codegen's XMM-only binop path hit a `RegLoc`
            # where it expected a `StackLoc`. Mirrors the existing
            # `module.CONST`-style `A.Attr` FFI-const handling elsewhere
            # in this file (see its own much longer comment for the
            # `value_windows` override rationale) -- same value
            # resolution, just for the from-import bare-name spelling
            # instead of the module-attribute spelling. `e.name not in
            # ctx.slot_ty` guards against a local variable shadowing the
            # imported constant's name (rare but real -- a function-local
            # `pi = 3` inside code that also imported `math.pi` at module
            # scope must read the LOCAL, not the FFI constant).
            b = ctx.mctx.ffi_consts[e.name]
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
            # Anything else (list-typed constants, etc.) falls through to
            # the generic path unchanged -- a separate, smaller,
            # not-yet-scoped gap, same as the module.CONST case's own
            # matching fallthrough note.
        # An unmodeled builtin referenced as a VALUE (`namespace.setdefault(
        # "print", print)`, `... = Exception`): `print`/`Exception`/`len`/...
        # are not user funcs, classes, builtin-type names, or ffi consts (all
        # handled above), and they were never assigned a real global slot, so
        # reading them through the generic global/slot path below loads an
        # UNINITIALIZED slot -- a garbage pointer. Boxed into an `any` slot and
        # later read back, that garbage flows into the read-choke's box-magic
        # dereference (`_lower_read_any_tag`) and faults on unmapped memory.
        # Yield a null (None) placeholder instead, the value-position analogue
        # of the graceful call stub (`_call_target_is_unresolvable`): the
        # reference survives as a harmless None as long as it isn't actually
        # invoked/used at runtime, exactly as an unmodeled builtin should.
        _in_active_comp = any(e.name in shadows for shadows in ctx.comprehension_shadows)
        if (
            e.name in _UNMODELED_BUILTIN_VALUES
            and not _is_global_name(ctx, e.name)
            and e.name not in ctx.slot_ty
            and e.name not in ctx.mctx.global_types
            and not _in_active_comp
        ):
            return _none_const(ctx)
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
        ptr = _name_value_ptr(ctx, e.name, ty)
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
        cond = _value_truthy_typed(ctx, left_v, A.expr_type(e.left), id(e))

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
        exp_ty = A.expr_type(e)
        res_ty = ir_type_for(exp_ty)
        tmp_name = f"__ifexp_{id(e)}"
        ptr = ctx.ensure_slot(tmp_name, res_ty)
        # An "any"-typed conditional expression (`<boxed> if c else None`,
        # `d.get(k) if k in d else x`, ...) is one more "any" slot: each arm
        # is stored through the write choke point so a boxed value stays
        # boxed and a concrete scalar is boxed, and the merged read
        # (auto-unboxed by `_lower_expr` at the consumer) recovers the kind.
        # Without this, `_lower_expr` on the arm auto-unboxed a boxed value
        # to its raw payload, and a later `type(<ifexp>)` saw an untagged
        # raw value (confirmed: `frame.stack.pop() if frame.stack else None`
        # -- portapy's VM RETURN -- lost the boxed int's kind).
        _arm = (
            (lambda a: _lower_value_into_any_slot(ctx, a))
            if exp_ty == "any"
            else (lambda a: _lower_expr(ctx, a))
        )

        then_b = ctx.new_block("ifexpthen")
        else_b = ctx.new_block("ifexpelse")
        merge_b = ctx.new_block("ifexpend")
        cond = _lower_truthy(ctx, e.test)
        ctx.emit(IRInstr("br.t", None, [cond, then_b.label, else_b.label]))

        ctx.switch_to(then_b)
        body_v = _arm(e.body)
        ctx.emit(IRInstr("store", None, [body_v, ptr]))
        ctx.emit(IRInstr("br", None, [merge_b.label]))

        ctx.switch_to(else_b)
        orelse_v = _arm(e.orelse)
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
            # Spill lhs to a stack slot before lowering rhs: rhs may be a
            # `str(<any>)` whose tag-dispatch formatter introduces control
            # flow (basic-block branches), across which a value held only in
            # a register is not guaranteed to survive. Without the spill, lhs
            # (e.g. a "prefix" literal) read back as garbage in some contexts
            # -- concretely, `"k=" + str(x)` inside a match case produced
            # "55" (lhs clobbered to rhs's value). The slot keeps lhs live.
            lhs = _lower_expr(ctx, e.left)
            lhs_slot = ctx.ensure_slot(f"__concat_lhs_{id(e)}", PTR)
            ctx.emit(IRInstr("store", None, [lhs, lhs_slot]))
            rhs = _lower_expr(ctx, e.right)
            lhs_kept = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", lhs_kept, [lhs_slot]))
            v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", v, ["_abi_str_concat", lhs_kept, rhs]))
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
        if lt == "set" and rt == "set" and e.op in ("&", "-"):
            # `s1 & s2` / `s1 - s2`: unlike `|` above, these were never
            # given a BinOp case at all -- only reachable via the
            # equivalent `.intersection()`/`.difference()` METHOD calls
            # (which already correctly route through _lower_set_setop,
            # just below in this same file). The operator forms instead
            # fell all the way through to the generic int-binop path much
            # further down, which treats both operands' raw HEADER
            # POINTERS as plain integers -- same corrupts-on-next-read
            # shape as the `list + list` bug documented just above (`&`
            # silently computed something that happened to still look
            # like a valid-ish pointer and produced a wrong answer rather
            # than crashing; `-` produced a pointer-difference small
            # enough to be a plausible-looking but bogus address,
            # confirmed via gdb: SIGSEGV on the very next dereference of
            # the "result"). codegen.py's own `_gen_binop` already treats
            # all three set operators identically via one shared
            # `_gen_set_setop` helper -- mirror that here by reusing this
            # file's own already-correct method-call path instead of
            # duplicating its logic.
            method = {"&": "intersection", "-": "difference"}[e.op]
            return _lower_set_setop(ctx, e.left, e.right, method, id(e))
        if lt == "float" or rt == "float" or e.op == "/":
            # `/` (true division) is ALWAYS float division in Python, even
            # for two int operands (`6 / 3` is `2.0`, not `2`) -- unlike
            # every other arithmetic op, which stays int-typed for two int
            # operands. `_BINOP` (the pure-int op table just below this
            # whole float-promotion block) has no `/` entry at all for
            # exactly this reason: reaching it with `/` and two int
            # operands would otherwise hit the "unsupported binop" fallback
            # a few lines down. Force this branch (which already handles
            # promoting a non-float operand via `sitofp`) even when NEITHER
            # operand is float-typed, so `int / int` still gets a real
            # float result instead of never being reachable at all.
            if e.op not in _FBINOP and e.op not in ("%", "**", "//"):
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
            if e.op in ("/", "//", "%"):
                _emit_float_divzero_check(ctx, b, e.op, id(e))
            if e.op in ("%", "**"):
                # No direct SSE instruction for float mod/pow -- both route
                # through the matching real libc double(double,double)
                # export (fmod/pow), same as codegen.py's
                # _emit_call_libc_double_double.
                v = ctx.tmp(F64)
                c_name = "fmod" if e.op == "%" else "pow"
                ctx.emit(IRInstr("call", v, [c_name, a, b]))
                return v
            if e.op == "//":
                # Float floor division: `a // b` is `floor(a / b)`. No
                # direct SSE instruction for either the divide-then-floor
                # combo or floor alone -- `fdiv` then a real libc
                # `floor(double)` call (a confirmed real msvcrt.dll export,
                # already used elsewhere in pe_linker.py's _DLL_FOR_SYMBOL).
                q = ctx.tmp(F64)
                ctx.emit(IRInstr("fdiv", q, [a, b]))
                v = ctx.tmp(F64)
                ctx.emit(IRInstr("call", v, ["floor", q]))
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
        if len(e.ops) == 1 and e.ops[0] in ("is", "is not"):
            bool_res = _lower_is_bool_literal(ctx, e)
            if bool_res is not None:
                return bool_res
            type_call, type_target = None, None
            if isinstance(e.operands[0], A.Call) and e.operands[0].func == "type" and len(e.operands[0].args) == 1:
                type_call, type_target = e.operands[0], e.operands[1]
            elif isinstance(e.operands[1], A.Call) and e.operands[1].func == "type" and len(e.operands[1].args) == 1:
                type_call, type_target = e.operands[1], e.operands[0]
            if type_call is not None:
                result = _lower_type_is_compare(ctx, type_call, type_target)
                if result is not None:
                    if e.ops[0] == "is not":
                        zero = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", zero, [0]))
                        inv = ctx.tmp(I64)
                        ctx.emit(IRInstr("icmp.eq", inv, [result, zero]))
                        return inv
                    return result
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
            if e.ops[0] in ("==", "!=") and lt0 == "dict" and rt0 == "dict":
                _vk_l = getattr(e.operands[0], "value_type", "int") or "int"
                _vk_r = getattr(e.operands[1], "value_type", "int") or "int"
                if _vk_l == _vk_r:
                    _deq = _lower_dict_eq(
                        ctx, e.operands[0], e.operands[1], _vk_l, id(e)
                    )
                    if e.ops[0] == "!=":
                        _dz = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", _dz, [0]))
                        _dinv = ctx.tmp(I64)
                        ctx.emit(IRInstr("icmp.eq", _dinv, [_deq, _dz]))
                        return _dinv
                    return _deq
            if (
                e.ops[0] in ("==", "!=")
                and lt0 in ("list", "tuple", "set")
                and rt0 in ("list", "tuple", "set")
                and (lt0 == "set") == (rt0 == "set")
            ):
                # Python container equality is STRUCTURAL. Without this these
                # fell through to the chained-comparison path below, which
                # compares the operands' raw pointers -- so `[1, 2] == [1, 2]`
                # was False, and `sorted(a) == sorted(b)` (the standard anagram
                # test) could never be True.
                if lt0 == "set":
                    # Two sets are equal iff each is a subset of the other --
                    # and given equal LENGTHS, one subset test is sufficient.
                    _sl = _lower_expr(ctx, e.operands[0])
                    _sr = _lower_expr(ctx, e.operands[1])
                    _sla = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", _sla, [_sl, _LIST_LEN_OFF]))
                    _slv = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", _slv, [_sla]))
                    _sra = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", _sra, [_sr, _LIST_LEN_OFF]))
                    _srv = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", _srv, [_sra]))
                    _same_len = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.eq", _same_len, [_slv, _srv]))
                    _sub = _lower_set_subset(ctx, e.operands[0], e.operands[1], id(e))
                    _ceq = ctx.tmp(I64)
                    ctx.emit(IRInstr("iand", _ceq, [_same_len, _sub]))
                else:
                    _el_l = _repr_el_kind(e.operands[0])
                    _el_r = _repr_el_kind(e.operands[1])
                    # Mixed element kinds can't be compared slot-for-slot by a
                    # single static rule; fall back to the pointer comparison
                    # that was the only behaviour before (never worse).
                    if _el_l != _el_r or _el_l in ("dict", "set"):
                        # Differing element kinds have no single slot-for-slot
                        # rule, and a dict/set element would need a different
                        # routine than the sequence walk. Both fall back to the
                        # pointer comparison that was the only behaviour
                        # before, which is never worse. A nested LIST/TUPLE
                        # element does recurse (see `_emit_sequence_eq_value`).
                        _ceq = None
                    else:
                        _ceq = _lower_sequence_eq(
                            ctx, e.operands[0], e.operands[1], _el_l, id(e)
                        )
                if _ceq is not None:
                    if e.ops[0] == "!=":
                        _z = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", _z, [0]))
                        _inv = ctx.tmp(I64)
                        ctx.emit(IRInstr("icmp.eq", _inv, [_ceq, _z]))
                        return _inv
                    return _ceq
            if lt0 == "set" and rt0 == "set" and e.ops[0] in ("<=", ">=", "<", ">"):
                # Set subset/superset comparisons (PEP-3119-style: `a <= b`
                # is `a.issubset(b)`, `a < b` is a proper subset i.e.
                # subset AND a != b, `>=`/`>` are the mirror via swapped
                # operands) -- ports codegen.py's own identical swap-
                # operands approach exactly.
                op = e.ops[0]
                swap = op in (">=", ">")
                sub_e, sup_e = (
                    (e.operands[1], e.operands[0]) if swap else (e.operands[0], e.operands[1])
                )
                result = _lower_set_subset(ctx, sub_e, sup_e, id(e))
                if op in ("<", ">"):
                    # Proper subset/superset: subset holds AND the two
                    # sets aren't equal (same length is sufficient given
                    # subset already held -- a subset of equal length must
                    # be the same set, matching codegen.py's own
                    # reasoning). Re-lower sub_e/sup_e here (a second real
                    # evaluation, same as codegen.py's own comment
                    # describes) rather than caching the first lowering's
                    # IRValues, since a length comparison needs the SAME
                    # underlying pointers _lower_set_subset already
                    # consumed inside its own loop -- simplest to just
                    # recompute rather than thread extra return values
                    # through that helper's signature.
                    sub_v2 = _lower_expr(ctx, sub_e)
                    sup_v2 = _lower_expr(ctx, sup_e)
                    sub_len_addr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", sub_len_addr, [sub_v2, _LIST_LEN_OFF]))
                    sub_len_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", sub_len_v, [sub_len_addr]))
                    sup_len_addr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", sup_len_addr, [sup_v2, _LIST_LEN_OFF]))
                    sup_len_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", sup_len_v, [sup_len_addr]))
                    not_equal = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.ne", not_equal, [sub_len_v, sup_len_v]))
                    proper = ctx.tmp(I64)
                    ctx.emit(IRInstr("iand", proper, [result, not_equal]))
                    return proper
                return result
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
            if op in ("is", "is not"):
                # `is`/`is not` are always raw-bit identity comparisons,
                # never a float-semantic compare -- matches codegen.py's
                # own SETCC comment exactly ("asmpython's uniform 8-byte
                # runtime representation" means is/is not on ANY type,
                # including float, is bit equality on the raw slot, not
                # fcmp). This matters for `Optional[float]` (`r is not
                # None`): sema collapses Optional[float] to plain "float"
                # (see parser.py's Optional-annotation handling) with no
                # separate None-tracking, so `None` here is the same
                # IntLit(0)-typed "int" sentinel every other Optional
                # uses -- comparing it against a REAL float value via
                # fcmp would be wrong even if it "worked" (0 promoted to
                # 0.0 loses the distinction between "is genuinely 0.0"
                # and "is None", which raw bit equality preserves). Was
                # entirely unimplemented for this case: fell through to
                # the float branch below and raised LowerError since
                # _FCMPOP has no is/is not entries at all (correctly --
                # they were never supposed to be looked up there).
                lv = operands[i]
                if lv.type is F64:
                    lv_i = ctx.tmp(I64)
                    ctx.emit(IRInstr("bitcast_f2i", lv_i, [lv]))
                    lv = lv_i
                rv = rhs
                if rv.type is F64:
                    rv_i = ctx.tmp(I64)
                    ctx.emit(IRInstr("bitcast_f2i", rv_i, [rhs]))
                    rv = rv_i
                ctx.emit(IRInstr(_CMPOP[op], step, [lv, rv]))
            elif operand_types[i] == "float" or rhs_ty == "float":
                if op not in _FCMPOP:
                    raise LowerError(f"unsupported float compare op {op!r}")
                lv = operands[i]
                if operand_types[i] != "float":
                    lv_f = ctx.tmp(F64)
                    ctx.emit(IRInstr("sitofp", lv_f, [lv]))
                    lv = lv_f
                rv = rhs
                if rhs_ty != "float":
                    rv_f = ctx.tmp(F64)
                    ctx.emit(IRInstr("sitofp", rv_f, [rhs]))
                    rv = rv_f
                ctx.emit(IRInstr(_FCMPOP[op], step, [lv, rv]))
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

    if isinstance(e, A.Call) and getattr(e, "starred_dynamic", False):
        # `f(*a)` where `f` is a CALLABLE VALUE and `a`'s length is a runtime
        # fact -- the decorator forwarding shape:
        #
        #     def deco(f):
        #         def wrap(*a):
        #             return f(*a)
        #
        # Neither the callee's arity nor len(a) is known at compile time, but
        # both are known at RUNTIME, so dispatch on the length: one fixed-arity
        # indirect call per candidate count. Same shape as the closure
        # capture-count dispatch, which solves the identical problem for
        # captures rather than arguments.
        MAX_STAR_ARITY = 8
        _sd_star = None
        _sd_fixed: list = []
        for _a in e.args:
            if isinstance(_a, A.Starred):
                _sd_star = _a
            else:
                _sd_fixed.append(_a)
        _sd_fn = _lower_expr(ctx, A.Name(name=e.func, pos=e.pos))
        _sd_pre = [_lower_expr(ctx, _a) for _a in _sd_fixed]
        _sd_seq = _lower_expr(ctx, _sd_star.value)
        _sd_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", _sd_len_addr, [_sd_seq, _LIST_LEN_OFF]))
        _sd_len = ctx.tmp(I64)
        ctx.emit(IRInstr("load", _sd_len, [_sd_len_addr]))
        _sd_buf_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", _sd_buf_addr, [_sd_seq, _LIST_BUF_OFF]))
        _sd_buf = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", _sd_buf, [_sd_buf_addr]))

        _sd_res_ty = ir_type_for(A.expr_type(e))
        _sd_res = ctx.ensure_slot(f"__stardyn_{id(e)}", _sd_res_ty)
        _sd_check = [ctx.new_block(f"stardynchk{n}_{id(e) % 9999}")
                     for n in range(MAX_STAR_ARITY + 1)]
        _sd_hit = [ctx.new_block(f"stardynhit{n}_{id(e) % 9999}")
                   for n in range(MAX_STAR_ARITY + 1)]
        _sd_none = ctx.new_block(f"stardynnone_{id(e) % 9999}")
        _sd_end = ctx.new_block(f"stardynend_{id(e) % 9999}")
        ctx.emit(IRInstr("br", None, [_sd_check[0].label]))
        for n in range(MAX_STAR_ARITY + 1):
            ctx.switch_to(_sd_check[n])
            _nv = ctx.tmp(I64)
            ctx.emit(IRInstr("const", _nv, [n]))
            _eq = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.eq", _eq, [_sd_len, _nv]))
            _nxt = (
                _sd_check[n + 1].label if n < MAX_STAR_ARITY else _sd_none.label
            )
            ctx.emit(IRInstr("br.t", None, [_eq, _sd_hit[n].label, _nxt]))
            ctx.switch_to(_sd_hit[n])
            _unpacked: list = []
            for i in range(n):
                _ea = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", _ea, [_sd_buf, i * 8]))
                _ev = ctx.tmp(I64)
                ctx.emit(IRInstr("load", _ev, [_ea]))
                _unpacked.append(_ev)
            _rv = ctx.tmp(_sd_res_ty)
            ctx.emit(IRInstr("call", _rv, [_sd_fn, *_sd_pre, *_unpacked]))
            ctx.emit(IRInstr("store", None, [_rv, _sd_res]))
            ctx.emit(IRInstr("br", None, [_sd_end.label]))
        ctx.switch_to(_sd_none)
        _zv = ctx.tmp(_sd_res_ty)
        ctx.emit(IRInstr("const", _zv, [0]))
        ctx.emit(IRInstr("store", None, [_zv, _sd_res]))
        ctx.emit(IRInstr("br", None, [_sd_end.label]))
        ctx.switch_to(_sd_end)
        _out = ctx.tmp(_sd_res_ty)
        ctx.emit(IRInstr("load", _out, [_sd_res]))
        return _out

    if isinstance(e, A.Call) and getattr(e, "dunder_call_on_expr", False):
        # `<expr>(args)` where the callee is an instance with `__call__`: a
        # DIRECT call to the resolved dunder with the evaluated receiver as
        # `self`, not an indirect call (the callee is an object, not a code
        # pointer).
        _dc_owner = e.dunder_call_owner
        recv_v = _lower_expr(ctx, e.func_expr)
        _dc_ann = _callee_param_annots(ctx, f"{_dc_owner}____call__")
        dc_args = [recv_v] + [
            _lower_call_arg(ctx, a, _dc_ann[i + 1] if i + 1 < len(_dc_ann) else None)
            for i, a in enumerate(e.args)
        ]
        dv = ctx.tmp(ir_type_for(A.expr_type(e)))
        ctx.emit(IRInstr("call", dv, [f"{_dc_owner}____call__", *dc_args]))
        return dv

    if isinstance(e, A.Call) and getattr(e, "callable_indirect", False):
        # `<expr>(args)` -- a call through a CALLABLE VALUE. Sema proved the
        # callee's static type is `callable:<ret>`, which is exactly the set of
        # values that lower to a bare code pointer (an `A.Lambda`, a function
        # reference, or a read of one back out of a dict value / list element /
        # instance field). The backend's "call" op is already an indirect call
        # whenever its target operand is an IRValue rather than a symbol name,
        # so there is nothing to add below the IR for this.
        #
        # Deliberately NOT the closure path: a CAPTURING closure is a heap
        # object `[magic, fn_ptr, caps...]`, not a code pointer, and the two
        # cannot be told apart at runtime without dereferencing (which would
        # fault on a real code pointer). Sema only ever stamps `callable:` on
        # the non-capturing form, so the discrimination stays static.
        fn_v = _lower_expr(ctx, e.func_expr)
        call_args = [_lower_expr(ctx, a) for a in e.args]
        res_ty = ir_type_for(A.expr_type(e))
        v = ctx.tmp(res_ty)
        ctx.emit(IRInstr("call", v, [fn_v, *call_args]))
        return v

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

    if (
        isinstance(e, A.Call)
        and e.func == "len"
        and len(e.args) == 1
        and A.expr_type(e.args[0]).startswith("instance:")
    ):
        # `len(obj)` on a user instance with a real `__len__` -- was
        # entirely unhandled (only str/list/tuple/dict/set/any/int
        # arguments to `len()` had a dispatch case), falling through to
        # a direct-symbol-call linking against a nonexistent symbol
        # `len`. Mirrors the same dunder-dispatch pattern established
        # throughout this file for `__bool__`/`__call__`/etc.
        cls_name = A.expr_type(e.args[0]).split(":", 1)[1]
        owner = _resolve_method_owner(ctx, cls_name, "__len__")
        if owner is None:
            raise LowerError(f"unsupported expr Call (len() on {cls_name!r} with no __len__)")
        obj_v = _lower_expr(ctx, e.args[0])
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", v, [f"{owner}____len__", obj_v]))
        return v

    if isinstance(e, A.Call) and e.func == "len" and len(e.args) == 1 and A.expr_type(e.args[0]) in ("dict", "set", "any", "int"):
        obj_v = _lower_expr(ctx, e.args[0])
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [obj_v, _LIST_LEN_OFF]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", v, [len_addr]))
        return v

    if isinstance(e, A.Call) and e.func == "bitcast_f2i" and len(e.args) == 1:
        if A.expr_type(e.args[0]) != "float":
            raise LowerError("unsupported expr Call (bitcast_f2i non-float)")
        f_v = _lower_expr(ctx, e.args[0])
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("bitcast_f2i", v, [f_v]))
        return v

    if isinstance(e, A.Call) and e.func == "bitcast_i2f" and len(e.args) == 1:
        if A.expr_type(e.args[0]) != "int":
            raise LowerError("unsupported expr Call (bitcast_i2f non-int)")
        i_v = _lower_expr(ctx, e.args[0])
        v = ctx.tmp(F64)
        ctx.emit(IRInstr("bitcast_i2f", v, [i_v]))
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
            # Each argument is coerced to a str, then SPILLED to a stack slot
            # before the next argument is lowered: an "any"-typed argument's
            # str coercion goes through `_lower_format_any_value`, whose
            # tag-dispatch introduces basic-block branches, across which an
            # earlier argument's result (held only in a register) is not
            # guaranteed to survive -- without the spill the first of
            # `print(x, y)` came out as garbage/"(null)". Reload all just
            # before the printf call. (Same hazard the str-concat lhs spill
            # addresses.)
            arg_slots: list = []
            for i, arg in enumerate(e.args):
                s_v = _lower_expr_as_str(ctx, arg)
                slot = ctx.ensure_slot(f"__printarg_{i}_{id(e)}", PTR)
                ctx.emit(IRInstr("store", None, [s_v, slot]))
                arg_slots.append(slot)
            call_args = []
            for slot in arg_slots:
                a_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", a_v, [slot]))
                call_args.append(a_v)
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

    if isinstance(e, A.Call) and e.func == "hasattr" and len(e.args) == 2:
        # hasattr(obj, name) -> dict_contains(obj, name) (0/1); None ->
        # False without dereferencing. Mirrors codegen.py's own
        # `hasattr` handling and this file's own `getattr` case just
        # above (same none_b/live_b/end_b shape). Was entirely
        # unimplemented: `hasattr` as a bare symbol fell through to a
        # direct-symbol-call linking against a nonexistent DLL import.
        obj_v = _lower_expr(ctx, e.args[0])
        name_v = _lower_expr(ctx, e.args[1])
        res_ptr = ctx.ensure_slot(f"__hasattr_res_{id(e)}", I64)
        none_b = ctx.new_block("hasattrnone")
        live_b = ctx.new_block("hasattrlive")
        end_b = ctx.new_block("hasattrend")
        zero = ctx.tmp(PTR if obj_v.type == PTR else I64)
        ctx.emit(IRInstr("const", zero, [0]))
        is_none = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", is_none, [obj_v, zero]))
        ctx.emit(IRInstr("br.t", None, [is_none, none_b.label, live_b.label]))

        ctx.switch_to(none_b)
        false_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", false_v, [0]))
        ctx.emit(IRInstr("store", None, [false_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(live_b)
        got_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", got_v, ["_abi_dict_contains", obj_v, name_v]))
        ctx.emit(IRInstr("store", None, [got_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))

        ctx.switch_to(end_b)
        out = ctx.tmp(I64)
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

    if isinstance(e, A.Call) and e.func == "id" and len(e.args) == 1:
        # Runtime objects are already represented by their stable pointer.
        # Scalar values use their immediate machine-word representation,
        # matching the compiler's documented lightweight id() model.
        return _lower_expr(ctx, e.args[0])

    if (
        isinstance(e, A.Call)
        and e.func == "zip"
        and len(e.args) >= 2
    ):
        # The compiled runtime models zip() eagerly as a list of tuple-layout
        # records (the same value produced by list(zip(...))). Reuse the
        # complete lockstep lowering below so a bare zip value can be passed
        # onward without leaving an unresolved native `zip` symbol.
        eager = A.Call(
            func="list",
            args=[e],
            pos=e.pos,
            inferred_type="list",
            list_el_type="tuple",
        )
        return _lower_expr(ctx, eager)

    if (
        isinstance(e, A.Call)
        and e.func == "list"
        and len(e.args) == 1
        and isinstance(e.args[0], A.Call)
        and e.args[0].func == "zip"
        and len(e.args[0].args) >= 2
    ):
        # `list(zip(A, B[, C...]))` -- builds a fresh list of N-element
        # tuples, walking the source lists in lockstep and stopping at
        # the shortest (real zip() truncation semantics). Ports
        # codegen.py's `_gen_list_zip` IR-op-for-instruction: each
        # iteration allocates a fresh tuple header+buffer (same layout
        # A.TupleLit uses) and appends its pointer via _abi_list_append
        # (which already handles pointer-typed elements, see e.g. the
        # dict.items()-pairs lowering). Was entirely unimplemented:
        # `zip` as a bare symbol fell through to a direct-symbol-call
        # linking against a nonexistent DLL import.
        zip_call = e.args[0]
        n = len(zip_call.args)
        it_ptrs = [ctx.ensure_slot(f"__lzip_it{k}_{id(e)}", PTR) for k in range(n)]
        el_tys = [_iter_element_type(ze) for ze in zip_call.args]
        for k, ze in enumerate(zip_call.args):
            v = _lower_expr(ctx, ze)
            ctx.emit(IRInstr("store", None, [v, it_ptrs[k]]))

        stop_ptr = ctx.ensure_slot(f"__lzip_stop_{id(e)}", I64)
        first_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", first_v, [it_ptrs[0]]))
        first_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", first_len_addr, [first_v, _LIST_LEN_OFF]))
        stop_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", stop_v, [first_len_addr]))
        ctx.emit(IRInstr("store", None, [stop_v, stop_ptr]))
        for k in range(1, n):
            cur_stop = ctx.tmp(I64)
            ctx.emit(IRInstr("load", cur_stop, [stop_ptr]))
            it_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", it_v, [it_ptrs[k]]))
            len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", len_addr, [it_v, _LIST_LEN_OFF]))
            len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", len_v, [len_addr]))
            is_shorter = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.lt", is_shorter, [len_v, cur_stop]))
            min_ptr = ctx.ensure_slot(f"__lzip_min_{k}_{id(e)}", I64)
            shorter_b = ctx.new_block(f"lzipmin_shorter_{k}")
            keep_b = ctx.new_block(f"lzipmin_keep_{k}")
            after_b = ctx.new_block(f"lzipmin_after_{k}")
            ctx.emit(IRInstr("br.t", None, [is_shorter, shorter_b.label, keep_b.label]))
            ctx.switch_to(shorter_b)
            ctx.emit(IRInstr("store", None, [len_v, min_ptr]))
            ctx.emit(IRInstr("br", None, [after_b.label]))
            ctx.switch_to(keep_b)
            ctx.emit(IRInstr("store", None, [cur_stop, min_ptr]))
            ctx.emit(IRInstr("br", None, [after_b.label]))
            ctx.switch_to(after_b)
            new_stop = ctx.tmp(I64)
            ctx.emit(IRInstr("load", new_stop, [min_ptr]))
            ctx.emit(IRInstr("store", None, [new_stop, stop_ptr]))

        res_ptr = ctx.ensure_slot(f"__lzip_res_{id(e)}", PTR)
        stop_v0 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", stop_v0, [stop_ptr]))
        one_cap = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_cap, [1]))
        real_cap_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", real_cap_v, [stop_v0, one_cap]))
        res_v0 = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", res_v0, ["_abi_new_list", real_cap_v]))
        ctx.emit(IRInstr("store", None, [res_v0, res_ptr]))

        idx_ptr = ctx.ensure_slot(f"__lzip_idx_{id(e)}", I64)
        zero_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_v, [0]))
        ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))

        head_b = ctx.new_block("lziphead")
        body_b = ctx.new_block("lzipbody")
        cont_b = ctx.new_block("lzipcont")
        end_b = ctx.new_block("lzipend")
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        stop_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", stop_v2, [stop_ptr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, stop_v2]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        idx_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
        n_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", n_v, [n]))
        tup_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", tup_v, ["_abi_new_list", n_v]))
        tup_buf_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", tup_buf_addr, [tup_v, _LIST_BUF_OFF]))
        tup_buf_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", tup_buf_v, [tup_buf_addr]))
        for k in range(n):
            it_v2 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", it_v2, [it_ptrs[k]]))
            addr = _list_elem_addr(ctx, it_v2, idx_v2)
            el_v = ctx.tmp(F64 if el_tys[k] == "float" else I64)
            ctx.emit(IRInstr("load", el_v, [addr]))
            store_v = el_v
            if el_tys[k] == "float":
                iv = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", iv, [el_v]))
                store_v = iv
            slot_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", slot_addr, [tup_buf_v, k * 8]))
            ctx.emit(IRInstr("store", None, [store_v, slot_addr]))
        tup_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", tup_len_addr, [tup_v, _LIST_LEN_OFF]))
        n_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("const", n_v2, [n]))
        ctx.emit(IRInstr("store", None, [n_v2, tup_len_addr]))

        res_v1 = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", res_v1, [res_ptr]))
        ctx.emit(IRInstr("call", None, ["_abi_list_append", res_v1, tup_v]))
        ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        next_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
        ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        final_res = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", final_res, [res_ptr]))
        return final_res

    if (
        isinstance(e, A.Call)
        and e.func == "list"
        and len(e.args) == 1
        and isinstance(e.args[0], A.Call)
        and e.args[0].func in ("filter", "map")
        and len(e.args[0].args) == 2
    ):
        # `list(filter(pred, xs))` / `list(map(fn, xs))` -- pred/fn is
        # either `None` (filter's truthy-test shorthand) or a lambda.
        # Unlike codegen.py's `_gen_list_filter`/`_gen_list_map` (which
        # inline the lambda BODY directly into the loop for speed),
        # this calls the lambda's own already-synthesized function
        # (every `A.Lambda` gets one, see sema.py's Lambda handling) --
        # simpler and equally correct, just one extra `call` per
        # element. Was entirely unimplemented: `filter`/`map` as bare
        # symbols fell through to a direct-symbol-call linking against
        # a nonexistent symbol.
        inner = e.args[0]
        fn_arg, xs_expr = inner.args[0], inner.args[1]
        is_filter = inner.func == "filter"
        is_truthy_filter = is_filter and A.is_none_expr(fn_arg)
        if not is_truthy_filter and not isinstance(fn_arg, A.Lambda):
            raise LowerError(f"unsupported expr Call ({inner.func}() with a non-lambda predicate)")
        xs_v = _lower_expr(ctx, xs_expr)
        xs_ptr = ctx.ensure_slot(f"__listcall_xs_{id(e)}", PTR)
        ctx.emit(IRInstr("store", None, [xs_v, xs_ptr]))
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [xs_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        one_cap = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_cap, [1]))
        real_cap_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", real_cap_v, [len_v, one_cap]))
        out_v0 = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v0, ["_abi_new_list", real_cap_v]))
        out_ptr = ctx.ensure_slot(f"__listcall_out_{id(e)}", PTR)
        ctx.emit(IRInstr("store", None, [out_v0, out_ptr]))
        idx_ptr = ctx.ensure_slot(f"__listcall_idx_{id(e)}", I64)
        zero_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_v, [0]))
        ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))
        el_ty = _iter_element_type(xs_expr)

        head_b = ctx.new_block("listcallhead")
        body_b = ctx.new_block("listcallbody")
        keep_b = ctx.new_block("listcallkeep") if is_filter else None
        skip_b = ctx.new_block("listcallskip") if is_filter else None
        cont_b = ctx.new_block("listcallcont")
        end_b = ctx.new_block("listcallend")
        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        idx_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
        xs_v2 = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", xs_v2, [xs_ptr]))
        addr = _list_elem_addr(ctx, xs_v2, idx_v2)
        elem_v = ctx.tmp(ir_type_for(el_ty))
        ctx.emit(IRInstr("load", elem_v, [addr]))

        if is_truthy_filter:
            # `filter(None, xs)` keeps the TRUTHY elements, and a str/container
            # element is falsy on its contents -- `filter(None, ['', 'a'])` kept
            # both, because an empty string is still a non-NULL pointer.
            keep_v = _value_truthy_typed(ctx, elem_v, el_ty, id(e))
            ctx.emit(IRInstr("br.t", None, [keep_v, keep_b.label, skip_b.label]))
            ctx.switch_to(keep_b)
            out_v1 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out_v1, [out_ptr]))
            ctx.emit(IRInstr("call", None, ["_abi_list_append", out_v1, elem_v]))
            ctx.emit(IRInstr("br", None, [cont_b.label]))
            ctx.switch_to(skip_b)
            ctx.emit(IRInstr("br", None, [cont_b.label]))
        elif is_filter:
            fn_name = fn_arg.func_name  # type: ignore[attr-defined]
            keep_v = ctx.tmp(I64)
            ctx.emit(IRInstr("call", keep_v, [fn_name, elem_v]))
            keep_truthy = _value_truthy(ctx, keep_v)
            ctx.emit(IRInstr("br.t", None, [keep_truthy, keep_b.label, skip_b.label]))
            ctx.switch_to(keep_b)
            out_v1 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out_v1, [out_ptr]))
            ctx.emit(IRInstr("call", None, ["_abi_list_append", out_v1, elem_v]))
            ctx.emit(IRInstr("br", None, [cont_b.label]))
            ctx.switch_to(skip_b)
            ctx.emit(IRInstr("br", None, [cont_b.label]))
        else:
            fn_name = fn_arg.func_name  # type: ignore[attr-defined]
            mapped_ty = ir_type_for(getattr(fn_arg, "lambda_ret", "int"))
            mapped_v = ctx.tmp(mapped_ty)
            ctx.emit(IRInstr("call", mapped_v, [fn_name, elem_v]))
            out_v1 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out_v1, [out_ptr]))
            store_v = mapped_v
            if mapped_ty is F64:
                iv = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", iv, [mapped_v]))
                store_v = iv
            ctx.emit(IRInstr("call", None, ["_abi_list_append", out_v1, store_v]))
            ctx.emit(IRInstr("br", None, [cont_b.label]))

        ctx.switch_to(cont_b)
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        next_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
        ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        final_out = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", final_out, [out_ptr]))
        return final_out

    if (
        isinstance(e, A.Call)
        and e.func in ("list", "tuple")
        and len(e.args) == 1
        and A.expr_type(e.args[0]).startswith("instance:")
    ):
        # `list(obj)` where obj is a user class with __iter__/__next__ -- which
        # is also what a generator function returns (sema desugars `yield` into
        # exactly such a class), so this covers `list(gen())` too. Draining an
        # iterator is precisely `[x for x in obj]`, so synthesize that
        # comprehension and reuse its iterator-protocol lowering (the
        # setjmp/StopIteration loop) rather than duplicating it here.
        _it_cls = A.expr_type(e.args[0]).split(":", 1)[1]
        if _resolve_method_owner(ctx, _it_cls, "__next__") is not None:
            _synth = A.Comprehension(
                elt=A.Name(name="_it", pos=e.pos, inferred_type=getattr(e, "list_el_type", "any")),
                var="_it",
                iter=e.args[0],
                cond=None,
                pos=e.pos,
                list_el_type=getattr(e, "list_el_type", "any"),
            )
            return _lower_comprehension_instance_iter(ctx, _synth, _it_cls)
        # Sequence protocol (`__len__` + `__getitem__`, e.g. deque): walk it by
        # index and append each element.
        _gi_owner = _resolve_method_owner(ctx, _it_cls, "__getitem__") or _it_cls
        _ln_owner = _resolve_method_owner(ctx, _it_cls, "__len__") or _it_cls
        obj_v = _lower_expr(ctx, e.args[0])
        n_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", n_v, [f"{_ln_owner}____len__", obj_v]))
        cap_v = ctx.tmp(I64)
        one_c = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_c, [1]))
        ctx.emit(IRInstr("iadd", cap_v, [n_v, one_c]))
        acc_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", acc_v, ["_abi_new_list", cap_v]))
        acc_ptr = ctx.ensure_slot(f"__lseq_out_{id(e)}", PTR)
        ctx.emit(IRInstr("store", None, [acc_v, acc_ptr]))
        si_ptr = ctx.ensure_slot(f"__lseq_idx_{id(e)}", I64)
        z_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", z_v, [0]))
        ctx.emit(IRInstr("store", None, [z_v, si_ptr]))
        sh_b = ctx.new_block("lseqhead")
        sb_b = ctx.new_block("lseqbody")
        se_b = ctx.new_block("lseqend")
        ctx.emit(IRInstr("br", None, [sh_b.label]))
        ctx.switch_to(sh_b)
        si_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", si_v, [si_ptr]))
        sgo_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", sgo_v, [si_v, n_v]))
        ctx.emit(IRInstr("br.t", None, [sgo_v, sb_b.label, se_b.label]))
        ctx.switch_to(sb_b)
        sbi_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", sbi_v, [si_ptr]))
        el_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", el_v, [f"{_gi_owner}____getitem__", obj_v, sbi_v]))
        scur_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", scur_v, [acc_ptr]))
        ctx.emit(IRInstr("call", None, ["_abi_list_append", scur_v, el_v]))
        s1_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", s1_v, [1]))
        sn_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", sn_v, [sbi_v, s1_v]))
        ctx.emit(IRInstr("store", None, [sn_v, si_ptr]))
        ctx.emit(IRInstr("br", None, [sh_b.label]))
        ctx.switch_to(se_b)
        sfin_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", sfin_v, [acc_ptr]))
        return sfin_v

    if (
        isinstance(e, A.Call)
        and e.func in ("list", "tuple")
        and len(e.args) == 1
        and A.expr_type(e.args[0]) in ("dict", "set")
    ):
        # `list(d)` / `list(s)` -- iterating a dict yields its keys and a set
        # its members, which is exactly the fresh list `_abi_dict_keys` builds
        # (dicts and sets share the instance layout). Previously `list(set)`
        # was rejected outright by sema and `list(dict)` reached no lowering
        # at all, falling through to a call of a nonexistent `list` symbol.
        src_v = _lower_expr(ctx, e.args[0])
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v, ["_abi_dict_keys", src_v]))
        return out_v

    if (
        isinstance(e, A.Call)
        and e.func in ("list", "tuple")
        and len(e.args) == 1
        and A.expr_type(e.args[0]) == "str"
    ):
        # `list("abc")` -> ['a', 'b', 'c']: one 1-character string per
        # position, the same `_abi_str_char_at` walk a str comprehension does.
        src_v = _lower_expr(ctx, e.args[0])
        slen_v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", slen_v, ["strlen", src_v]))
        cap_v = ctx.tmp(I64)
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        ctx.emit(IRInstr("iadd", cap_v, [slen_v, one_v]))
        lst_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", lst_v, ["_abi_new_list", cap_v]))
        lst_ptr = ctx.ensure_slot(f"__lstr_out_{id(e)}", PTR)
        ctx.emit(IRInstr("store", None, [lst_v, lst_ptr]))
        idx_ptr = ctx.ensure_slot(f"__lstr_idx_{id(e)}", I64)
        zero_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_v, [0]))
        ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))
        lh_b = ctx.new_block("lstrhead")
        lb_b = ctx.new_block("lstrbody")
        le_b = ctx.new_block("lstrend")
        ctx.emit(IRInstr("br", None, [lh_b.label]))
        ctx.switch_to(lh_b)
        i_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", i_v, [idx_ptr]))
        go_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", go_v, [i_v, slen_v]))
        ctx.emit(IRInstr("br.t", None, [go_v, lb_b.label, le_b.label]))
        ctx.switch_to(lb_b)
        bi_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", bi_v, [idx_ptr]))
        ch_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", ch_v, ["_abi_str_char_at", src_v, bi_v]))
        cur_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_v, [lst_ptr]))
        ctx.emit(IRInstr("call", None, ["_abi_list_append", cur_v, ch_v]))
        st_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", st_v, [1]))
        ni_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", ni_v, [bi_v, st_v]))
        ctx.emit(IRInstr("store", None, [ni_v, idx_ptr]))
        ctx.emit(IRInstr("br", None, [lh_b.label]))
        ctx.switch_to(le_b)
        fin_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", fin_v, [lst_ptr]))
        return fin_v

    if (
        isinstance(e, A.Call)
        and e.func in ("list", "tuple")
        and len(e.args) == 1
        and A.expr_type(e.args[0]) in ("list", "tuple", "any")
    ):
        # `list(x)`/`tuple(x)` from an existing list/tuple source: a
        # shallow copy (they share the same heap layout, so a full-range
        # slice IS a shallow copy). Mirrors codegen.py's `_gen_list_call`
        # simple case exactly -- the `filter(...)`/`map(...)`/`zip(...)`-
        # wrapped forms it also special-cases are separate, larger
        # features not covered here. Was entirely unhandled: `list(...)`
        # as a plain value (not a `for` loop's own iterable, which has
        # unrelated special-case handling) fell through to a
        # direct-symbol-call linking against a nonexistent symbol
        # `list`.
        src_v = _lower_expr(ctx, e.args[0])
        min_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", min_v, [-9223372036854775808]))
        max_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", max_v, [9223372036854775807]))
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v, ["_abi_list_slice", src_v, min_v, max_v]))
        return out_v

    if isinstance(e, A.Call) and e.func == "dict" and len(e.args) in (0, 1):
        # `dict()` -> fresh empty dict; `dict(other)` -> shallow copy (via
        # dict.update's own merge helper); `dict(pairs)` (sema flags this
        # shape via `e.dict_from_pairs`) -> iterate a list of 2-element
        # tuples/lists and insert each (k, v). Mirrors codegen.py's
        # `_gen_dict_call` exactly, including reading each pair's raw
        # 8-byte key/value cells directly (no _lower_dict_key conversion
        # needed -- a str key's cell IS already the interned-string
        # pointer _abi_dict_set wants). Was entirely unimplemented: `dict`
        # as a bare symbol fell through to a direct-symbol-call linking
        # against a nonexistent DLL import.
        if not e.args:
            out_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out_v, ["_abi_new_instance"]))
            return out_v
        if getattr(e, "dict_from_pairs", False):
            pairs_v = _lower_expr(ctx, e.args[0])
            pairs_ptr = ctx.ensure_slot(f"__dfp_it_{id(e)}", PTR)
            ctx.emit(IRInstr("store", None, [pairs_v, pairs_ptr]))
            len_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", len_addr, [pairs_v, _LIST_LEN_OFF]))
            len_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", len_v, [len_addr]))
            idx_ptr = ctx.ensure_slot(f"__dfp_idx_{id(e)}", I64)
            zero_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero_v, [0]))
            ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))
            res_v0 = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", res_v0, ["_abi_new_instance"]))
            res_ptr = ctx.ensure_slot(f"__dfp_res_{id(e)}", PTR)
            ctx.emit(IRInstr("store", None, [res_v0, res_ptr]))

            head_b = ctx.new_block("dfphead")
            body_b = ctx.new_block("dfpbody")
            end_b = ctx.new_block("dfpend")
            ctx.emit(IRInstr("br", None, [head_b.label]))

            ctx.switch_to(head_b)
            idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
            cond_v = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
            ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

            ctx.switch_to(body_b)
            idx_v2 = ctx.tmp(I64)
            ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
            pairs_v2 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", pairs_v2, [pairs_ptr]))
            pair_addr = _list_elem_addr(ctx, pairs_v2, idx_v2)
            pair_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", pair_v, [pair_addr]))
            pair_buf_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", pair_buf_addr, [pair_v, _LIST_BUF_OFF]))
            pair_buf_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", pair_buf_v, [pair_buf_addr]))
            key_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", key_addr, [pair_buf_v, 0]))
            key_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", key_v, [key_addr]))
            val_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", val_addr, [pair_buf_v, 8]))
            val_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", val_v, [val_addr]))
            res_v1 = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", res_v1, [res_ptr]))
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", res_v1, key_v, val_v]))
            one_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", one_v, [1]))
            next_idx = ctx.tmp(I64)
            ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
            ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
            ctx.emit(IRInstr("br", None, [head_b.label]))

            ctx.switch_to(end_b)
            final_res = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", final_res, [res_ptr]))
            return final_res
        # dict(other): shallow copy via the same merge helper dict.update()
        # uses.
        src_v = _lower_expr(ctx, e.args[0])
        out_v0 = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v0, ["_abi_new_instance"]))
        ctx.emit(IRInstr("call", None, ["_abi_dict_update", out_v0, src_v]))
        return out_v0

    if isinstance(e, A.Call) and e.func in ("set", "frozenset") and len(e.args) in (0, 1):
        # `set(x)`/`frozenset(x)` -- sets are dict-backed (str-keyed,
        # dummy int value 1 per member) in this codebase, matching
        # codegen.py's `_gen_set_call` exactly. Was entirely
        # unimplemented as a plain value expression: `set()`/`set(xs)`
        # fell through to a direct-symbol-call linking against a
        # nonexistent symbol `set`.
        if not e.args:
            out_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out_v, ["_abi_new_instance"]))
            return out_v
        arg_t = A.expr_type(e.args[0])
        if arg_t in ("set", "dict", "any"):
            # Already dict-backed -- hand it straight back (sets/dicts
            # share membership semantics closely enough that this
            # codebase's set implementation reuses the same backing
            # store, same as codegen.py's identical shortcut).
            return _lower_expr(ctx, e.args[0])
        if arg_t not in ("list", "tuple"):
            raise LowerError(f"unsupported expr Call (set() from {arg_t!r})")
        # `src_v`/`out_v` are stored into slots and RELOADED at every
        # use (rather than reusing the same SSA temp across the loop's
        # back-edge) -- confirmed via a real crash + register-allocation
        # dump that reusing the raw temps let the allocator place a
        # same-block, later-defined value (the per-iteration dummy `1`
        # dict-set value) into the SAME physical register as the
        # still-needed source-list pointer, since a plain last-use scan
        # saw the list pointer's last read as happening BEFORE that
        # later temp's definition point in the SAME block -- on the
        # second iteration the "list pointer" register actually held
        # `1`, and dereferencing `1+8` segfaulted. Every other loop
        # helper in this file (_lower_for_zip, _lower_os_listdir, etc.)
        # already uses this store-and-reload pattern for exactly this
        # reason; this was the one loop that didn't.
        src_v0 = _lower_expr(ctx, e.args[0])
        src_ptr = ctx.ensure_slot(f"__setcall_src_{id(e)}", PTR)
        ctx.emit(IRInstr("store", None, [src_v0, src_ptr]))
        out_v0 = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v0, ["_abi_new_instance"]))
        out_ptr = ctx.ensure_slot(f"__setcall_out_{id(e)}", PTR)
        ctx.emit(IRInstr("store", None, [out_v0, out_ptr]))
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [src_v0, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        idx_ptr = ctx.ensure_slot(f"__setcall_idx_{id(e)}", I64)
        zero_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero_v, [0]))
        ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))
        el_ty = _iter_element_type(e.args[0])

        head_b = ctx.new_block("setcallhead")
        body_b = ctx.new_block("setcallbody")
        end_b = ctx.new_block("setcallend")
        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        idx_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
        src_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", src_v, [src_ptr]))
        addr = _list_elem_addr(ctx, src_v, idx_v2)
        elem_v = ctx.tmp(ir_type_for(el_ty))
        ctx.emit(IRInstr("load", elem_v, [addr]))
        if el_ty == "int":
            base10 = ctx.tmp(I64)
            ctx.emit(IRInstr("const", base10, [10]))
            empty_name = ctx.mctx.intern_str("")
            empty_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
            key_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", key_v, ["_abi_int_to_base", elem_v, base10, empty_v]))
        else:
            key_v = elem_v
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", out_v, [out_ptr]))
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", out_v, key_v, one_v]))
        next_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
        ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        final_out = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", final_out, [out_ptr]))
        return final_out

    if isinstance(e, A.Call) and e.func == "sum" and len(e.args) in (1, 2):
        # `sum(xs[, start])`: accumulate over a list/tuple buffer. Integer by
        # default; FLOAT (xmm slot + fadd) when sema typed this call float --
        # a float-element list/tuple or a float `start` (see the sum result
        # typing in sema). Loading a float element into an int accumulator and
        # `iadd`-ing added the raw bit patterns as integers, returning garbage.
        is_f = A.expr_type(e) == "float"
        acc_ty = F64 if is_f else I64
        add_op = "fadd" if is_f else "iadd"
        xs_v = _lower_expr(ctx, e.args[0])
        acc_ptr = ctx.ensure_slot(f"__sum_acc_{id(e)}", acc_ty)
        if len(e.args) == 2:
            start_v = _lower_expr(ctx, e.args[1])
            if is_f and A.expr_type(e.args[1]) != "float":
                start_f = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", start_f, [start_v]))
                start_v = start_f
            ctx.emit(IRInstr("store", None, [start_v, acc_ptr]))
        else:
            zero0 = ctx.tmp(acc_ty)
            ctx.emit(IRInstr("const", zero0, [0.0 if is_f else 0]))
            ctx.emit(IRInstr("store", None, [zero0, acc_ptr]))
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [xs_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        idx_ptr = ctx.ensure_slot(f"__sum_idx_{id(e)}", I64)
        zero1 = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero1, [0]))
        ctx.emit(IRInstr("store", None, [zero1, idx_ptr]))

        head_b = ctx.new_block("sumhead")
        body_b = ctx.new_block("sumbody")
        end_b = ctx.new_block("sumend")
        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        idx_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
        addr = _list_elem_addr(ctx, xs_v, idx_v2)
        # An "any"-element list holds BOXED scalars, so the element has to be
        # unboxed before it can be added -- a raw load accumulated the elements'
        # box ADDRESSES. `_load_list_elem` yields the payload; for a float sum
        # that payload is the double's bit pattern, so reinterpret rather than
        # convert (the same distinction `float(<any>)` makes).
        sum_el_ty = _iter_element_type(e.args[0])
        if sum_el_ty == "any":
            payload = _load_list_elem(ctx, addr, "any")
            if is_f:
                elem_v = ctx.tmp(F64)
                ctx.emit(IRInstr("bitcast_i2f", elem_v, [payload]))
            else:
                elem_v = payload
        else:
            elem_v = ctx.tmp(acc_ty)
            ctx.emit(IRInstr("load", elem_v, [addr]))
        acc_v = ctx.tmp(acc_ty)
        ctx.emit(IRInstr("load", acc_v, [acc_ptr]))
        new_acc = ctx.tmp(acc_ty)
        ctx.emit(IRInstr(add_op, new_acc, [acc_v, elem_v]))
        ctx.emit(IRInstr("store", None, [new_acc, acc_ptr]))
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        next_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
        ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        final_v = ctx.tmp(acc_ty)
        ctx.emit(IRInstr("load", final_v, [acc_ptr]))
        return final_v

    if isinstance(e, A.Call) and e.func in ("any", "all") and len(e.args) == 1:
        # `any(xs)`/`all(xs)`: scan a list/tuple buffer testing each
        # element for truthiness, short-circuiting on the first hit.
        # Mirrors codegen.py's raw-8-byte-slot scan (elements are
        # int/ptr-typed cells; float elements would need _value_truthy,
        # not exercised by any test case yet). Was entirely
        # unimplemented: `any`/`all` as bare symbols fell through to a
        # direct-symbol-call linking against a nonexistent DLL import.
        is_all = e.func == "all"
        # The element kind decides truthiness: a str/container element is falsy
        # on its CONTENTS, so `any(['', ''])` was True (both are non-NULL
        # pointers) until this was threaded through.
        el_kind_aa = _repr_el_kind(e.args[0])
        xs_v = _lower_expr(ctx, e.args[0])
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [xs_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        idx_ptr = ctx.ensure_slot(f"__aa_idx_{id(e)}", I64)
        zero0 = ctx.tmp(I64)
        ctx.emit(IRInstr("const", zero0, [0]))
        ctx.emit(IRInstr("store", None, [zero0, idx_ptr]))

        res_ptr = ctx.ensure_slot(f"__aa_res_{id(e)}", I64)
        head_b = ctx.new_block("aahead")
        body_b = ctx.new_block("aabody")
        cont_b = ctx.new_block("aacont")
        hit_b = ctx.new_block("aahit")
        end_b = ctx.new_block("aaend")
        join_b = ctx.new_block("aajoin")
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cond_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
        ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        idx_v2 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
        addr = _list_elem_addr(ctx, xs_v, idx_v2)
        elem_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", elem_v, [addr]))
        truthy_v = _value_truthy_typed(ctx, elem_v, el_kind_aa, id(e))
        if is_all:
            ctx.emit(IRInstr("br.t", None, [truthy_v, cont_b.label, hit_b.label]))
        else:
            ctx.emit(IRInstr("br.t", None, [truthy_v, hit_b.label, cont_b.label]))

        ctx.switch_to(cont_b)
        one_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", one_v, [1]))
        next_idx = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
        ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(hit_b)
        hit_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", hit_v, [0 if is_all else 1]))
        ctx.emit(IRInstr("store", None, [hit_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [join_b.label]))

        ctx.switch_to(end_b)
        end_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", end_v, [1 if is_all else 0]))
        ctx.emit(IRInstr("store", None, [end_v, res_ptr]))
        ctx.emit(IRInstr("br", None, [join_b.label]))

        ctx.switch_to(join_b)
        final_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", final_v, [res_ptr]))
        return final_v

    if isinstance(e, A.Call) and e.func == "range" and len(e.args) in (1, 2, 3):
        # `range(...)` used as a real VALUE (e.g. `list(range(5))`), not
        # a `for` loop's own iterable -- that shape is lowered specially
        # (via `s.range_args`) and never reaches this generic A.Call
        # path at all. Materializes a real list[int], matching
        # codegen.py's `_runtime_range_list` exactly (now wired up via a
        # new `_abi_range_list` shim, previously nonexistent -- this
        # whole shape was entirely unhandled, falling through to a
        # direct-symbol-call linking against a nonexistent symbol
        # `range`).
        if len(e.args) == 1:
            start_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", start_v, [0]))
            stop_v = _lower_expr(ctx, e.args[0])
            step_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", step_v, [1]))
        elif len(e.args) == 2:
            start_v = _lower_expr(ctx, e.args[0])
            stop_v = _lower_expr(ctx, e.args[1])
            step_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", step_v, [1]))
        else:
            start_v = _lower_expr(ctx, e.args[0])
            stop_v = _lower_expr(ctx, e.args[1])
            step_v = _lower_expr(ctx, e.args[2])
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v, ["_abi_range_list", start_v, stop_v, step_v]))
        return out_v

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
            if getattr(e, "el_type", "") == "any":
                # Declared heterogeneous (explicit list[object], or the list a
                # call packs an unannotated *args into): elements are read back
                # through the "any" path, so they must be BOXED going in.
                val = _lower_value_into_any_slot(ctx, el)
            else:
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
            # A value stored into an "any"-valued dict literal is routed
            # through the store choke point, so its kind survives for a later
            # `d[k]` read.
            #
            # This used to fire ONLY for an explicitly annotated
            # `dict[str, object]` (sema's `box_values`), on the grounds that a
            # bare `{...}` with merely-mixed values "is consumed raw by
            # bare-dict readers that never unbox, so boxing would break a
            # `d[k] == "x"` compare". That is no longer true: an "any"-valued
            # dict read is typed PTR at the subscript site precisely so the
            # read choke unboxes it, the same way a list literal's elements
            # were already handled. Leaving bare mixed dicts unboxed meant
            # `{"a": 1, "b": [2, 3]}` handed `isinstance()` a value with no
            # tag -- so json's encoder could not tell an int from a nested
            # list and serialised the list as its pointer.
            if getattr(e, "box_values", False) or getattr(e, "value_type", "") == "any":
                val = _lower_value_into_any_slot(ctx, v)
            else:
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
            SENTINEL_MIN = -9223372036854775808
            SENTINEL_MAX = 9223372036854775807
            if obj_ty in ("list", "tuple") and e.index.step is None:
                obj_v = _lower_expr(ctx, e.obj)
                if e.index.start is None:
                    start_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", start_v, [SENTINEL_MIN]))
                else:
                    start_v = _lower_expr(ctx, e.index.start)
                if e.index.stop is None:
                    stop_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", stop_v, [SENTINEL_MAX]))
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
            if obj_ty in ("list", "tuple") and e.index.step is not None:
                # xs[start:stop:step] -- _abi_list_slice_step mirrors
                # codegen.py's own _gen_list_slice's step branch exactly:
                # SENTINEL_MIN for a missing start, SENTINEL_MAX for a
                # missing stop (list's runtime helper always uses this
                # fixed pair, unlike the str version below). Was entirely
                # unimplemented: any list/tuple slice with an explicit
                # step fell through to a LowerError.
                obj_v = _lower_expr(ctx, e.obj)
                if e.index.start is None:
                    start_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", start_v, [SENTINEL_MIN]))
                else:
                    start_v = _lower_expr(ctx, e.index.start)
                if e.index.stop is None:
                    stop_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", stop_v, [SENTINEL_MAX]))
                else:
                    stop_v = _lower_expr(ctx, e.index.stop)
                step_v = _lower_expr(ctx, e.index.step)
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_list_slice_step", obj_v, start_v, stop_v, step_v]))
                return v
            if obj_ty == "str" and e.index.step is not None:
                # s[start:stop:step] -- _abi_str_slice_step mirrors
                # codegen.py's own _gen_str_slice_step exactly: a missing
                # stop ALWAYS passes SENTINEL_MIN (not MAX), regardless of
                # step's sign -- the runtime helper inspects step itself
                # to pick the direction-correct default. Was entirely
                # unimplemented: any str slice with an explicit step fell
                # through to a LowerError.
                obj_v = _lower_expr(ctx, e.obj)
                if e.index.start is None:
                    start_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", start_v, [SENTINEL_MIN]))
                else:
                    start_v = _lower_expr(ctx, e.index.start)
                if e.index.stop is None:
                    stop_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", stop_v, [SENTINEL_MIN]))
                else:
                    stop_v = _lower_expr(ctx, e.index.stop)
                step_v = _lower_expr(ctx, e.index.step)
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_slice_step", obj_v, start_v, stop_v, step_v]))
                return v
            if obj_ty == "any":
                # Slice of an opaque ("any") value -- a param, an attribute
                # (e.g. `self.__mro__[1:]`), an object-typed element. The
                # value may at runtime be a str or a list/tuple, so dispatch
                # on its runtime shape (str-box tag vs raw container) exactly
                # as CPython slices by type. Both arms return a pointer, which
                # the "any" result type carries fine. Was a hard LowerError.
                return _lower_slice_any(ctx, e, SENTINEL_MIN, SENTINEL_MAX)
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
            res_ty = A.expr_type(e)
            res_is_float = res_ty == "float"
            obj_v = _lower_expr(ctx, e.obj)
            key_v = _lower_dict_key(ctx, e.index)
            _emit_dict_key_check(ctx, obj_v, key_v, id(e))
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            # A bare / `any`-valued dict stores its values BOXED (the store
            # choke `_lower_value_into_any_slot`). Type the fetched value PTR so
            # `_lower_expr`'s read choke actually unboxes it -- that choke only
            # unboxes an "any"-typed value whose IR type is PTR, and an I64
            # result slipped straight past it, so `d[k] + 1`, `y: int = d[k]`,
            # and a typed `return self.m[k]` all used the raw box pointer as an
            # int and produced garbage (a scalar payload the box wrapped). A
            # homogeneous `dict[str, V]` stores its values raw and is typed V
            # (not "any") here, so it keeps the I64 path unchanged. Unboxing is a
            # safe no-op on the non-scalar values (lists/instances) an "any"
            # dict passes through unboxed, so this is correct for them too.
            v = ctx.tmp(PTR if res_ty == "any" else I64)
            ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_v, key_v, zero]))
            if res_is_float:
                # Same int-only-cell constraint as dict.get()'s own
                # bitcast_i2f (and A.Attr's matching case) -- was
                # previously a hard LowerError here specifically, even
                # though dict.get() on the identical float-valued dict
                # already worked; a plain `d[key]` read had just never
                # been extended to match.
                fv = ctx.tmp(F64)
                ctx.emit(IRInstr("bitcast_i2f", fv, [v]))
                return fv
            return v
        getitem_cls = getattr(e, "_getitem_class", "")
        if getitem_cls or obj_ty.startswith("instance:"):
            cls_name = getitem_cls or obj_ty.split(":", 1)[1]
            obj_v = _lower_expr(ctx, e.obj)
            idx_v = _lower_expr(ctx, e.index)
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [f"{cls_name}____getitem__", obj_v, idx_v]))
            return v
        if obj_ty == "inparam":
            # `items[i]`: outparam's read-side counterpart -- e.obj's own
            # IRValue already IS the raw caller-owned array base pointer
            # (an exported function's inparam[T] parameter lowers PTR-
            # typed, same as outparam's; see ir_type_for's default), so no
            # list-header/buffer-pointer indirection is needed, only the
            # index*elem_size byte-offset arithmetic (_inparam_elem_addr).
            el_kind = ctx.slot_el_ty.get(e.obj.name, "int") if isinstance(e.obj, A.Name) else "int"
            result_ty = A.expr_type(e)
            ptr_v = _lower_expr(ctx, e.obj)
            idx_v = _lower_expr(ctx, e.index)
            elem_size = _INPARAM_OUTPARAM_ELEM_SIZE.get(el_kind, 8)
            addr = _inparam_elem_addr(ctx, ptr_v, idx_v, elem_size=elem_size)
            if el_kind == "int8":
                v = ctx.tmp(U8)
                ctx.emit(IRInstr("load", v, [addr]))
                # Widen the raw byte read to an ordinary I64 value -- every
                # other consumer of an "int"-typed IRValue expects I64, and
                # this int8-pointee value is only ever used as a plain
                # Python-level int (0-255), never re-stored at 1-byte width.
                widened = ctx.tmp(I64)
                ctx.emit(IRInstr("zext", widened, [v]))
                return widened
            if el_kind == "int32":
                v = ctx.tmp(I32)
                ctx.emit(IRInstr("load", v, [addr]))
                widened = ctx.tmp(I64)
                ctx.emit(IRInstr("sext", widened, [v]))
                return widened
            v = ctx.tmp(F64 if result_ty == "float" else I64)
            ctx.emit(IRInstr("load", v, [addr]))
            return v
        if obj_ty == "any" and A.expr_type(e.index) == "str":
            # `opaque[strkey]` -- an "any"-typed object indexed by a STRING is
            # a dict access at runtime (a list/tuple only ever takes an int
            # index). The generic "any is a list" fallthrough below would run
            # the string key through the integer-index list path and read
            # garbage / fault. Route it through the same dict-get the
            # `obj_ty == "dict"` case uses. Reached for a heterogeneous list's
            # elements (`items: list = [some_tuple, some_dict]; items[1][k]`),
            # whose element static type collapses to "any".
            _res_ty = A.expr_type(e)
            res_is_float = _res_ty == "float"
            obj_v = _lower_expr(ctx, e.obj)
            key_v = _lower_dict_key(ctx, e.index)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            # Same PTR typing as the `obj_ty == "dict"` read above, and for the
            # same reason: an "any" result must be typed PTR or `_lower_expr`'s
            # read choke skips it and the box POINTER flows on as if it were
            # the value. This path is the one a bundled module takes when it
            # walks an opaque object as a dict (`obj[k]` inside
            # `def f(obj: object)`), which is exactly what json's encoder does.
            v = ctx.tmp(PTR if _res_ty == "any" else I64)
            ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_v, key_v, zero]))
            if res_is_float:
                fv = ctx.tmp(F64)
                ctx.emit(IRInstr("bitcast_i2f", fv, [v]))
                return fv
            return v
        if obj_ty not in ("list", "tuple", "any"):
            raise LowerError(f"unsupported expr Subscript ({obj_ty})")
        # obj_ty == "any" (e.g. a lambda parameter -- sema.py seeds every
        # lambda parameter's type as "any" unconditionally, see
        # _lower_sort_key_call's docstring) is treated the same as list/
        # tuple: at runtime it IS a list/tuple-layout pointer, this is only
        # a static-typing gap. Mirrors codegen.py's `_gen_subscript`, whose
        # int-index fallthrough (past the dict/str special cases) accepts
        # ANY obj_t uniformly -- there's no separate "any" rejection there
        # at all. Was reachable via `min(pairs, key=lambda p: p[1])`: the
        # call SITE already has a dedicated tuple-index fast path
        # (`_lower_sort_key_call`) that never lowers the lambda body, but
        # the synthesized lambda function itself is still marked reachable
        # and lowered independently (its `return p[1]` hits this exact
        # generic Subscript path with `p`'s static type "any"), so the
        # LowerError fired even though the fast path made the call-site
        # lowering itself correct.
        result_ty = A.expr_type(e)
        obj_v = _lower_expr(ctx, e.obj)
        idx_v = _lower_expr(ctx, e.index)
        if A.expr_type(e.index) == "any":
            # `obj[index]` where the index is opaque: at runtime it may be an
            # int (element access) OR a slice object (`slice(a, b, c)` -- e.g.
            # a bytecode VM's BUILD_SLICE feeding GET_ITEM). Branch on the
            # index's runtime tag: a slice-tagged cell dispatches to the
            # list/str slice helper reading its start/stop/step; anything else
            # is an ordinary integer element load. Only meaningful when the
            # container is a real list/tuple/str at runtime (this "any"-object
            # path already assumes that, see the note above).
            return _lower_dynamic_slice_or_index(ctx, obj_v, idx_v, result_ty, id(e))
        _emit_list_index_bounds_check(ctx, obj_v, idx_v, id(e))
        addr = _list_elem_addr(ctx, obj_v, idx_v)
        # An "any"-element list stores its elements BOXED (the store choke
        # `_lower_value_into_any_slot`, reached from the ListLit lowering and
        # from `.append` into such a list), so the load has to be typed PTR for
        # `_lower_expr`'s read choke to unbox it -- that choke only unboxes an
        # "any"-typed value whose IR type is PTR, and an I64 result slips
        # straight past it, leaving the raw box POINTER in play as if it were
        # the value. Exactly the dict-element case already handled above, and
        # the same reason: `xs[i] + xs[j]` added two box addresses and handed
        # the sum to print(), which read its tag by dereferencing it. A
        # homogeneous list is typed by its element kind (not "any") and keeps
        # the I64/F64 path unchanged; unboxing is a safe no-op on the
        # never-boxed pointer elements (lists/dicts/instances) an "any" list
        # passes through, so those stay correct too.
        v = ctx.tmp(F64 if result_ty == "float" else (PTR if result_ty == "any" else I64))
        ctx.emit(IRInstr("load", v, [addr]))
        return v

    if (
        isinstance(e, A.MethodCall)
        and isinstance(e.obj, A.Name)
        and e.obj.name == "str"
        and e.method == "maketrans"
        and 2 <= len(e.args) <= 3
    ):
        # `str.maketrans(frm, to[, delete])` -> a dict from each single-char
        # string to its replacement; a deleted character maps to "". Walk the
        # inputs in lockstep (they are equal length -- CPython requires it) and
        # then map every `delete` character to the empty string.
        frm_v = _lower_expr(ctx, e.args[0])
        to_v = _lower_expr(ctx, e.args[1])
        tbl_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", tbl_v, ["_abi_new_instance"]))
        mt_len = ctx.tmp(I64)
        ctx.emit(IRInstr("call", mt_len, ["strlen", frm_v]))
        mt_idx = ctx.ensure_slot(f"__mktr_i_{id(e)}", I64)
        mt_z = ctx.tmp(I64)
        ctx.emit(IRInstr("const", mt_z, [0]))
        ctx.emit(IRInstr("store", None, [mt_z, mt_idx]))
        mh_b = ctx.new_block("mktrhead")
        mb_b = ctx.new_block("mktrbody")
        me_b = ctx.new_block("mktrend")
        ctx.emit(IRInstr("br", None, [mh_b.label]))
        ctx.switch_to(mh_b)
        mi_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", mi_v, [mt_idx]))
        mgo_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", mgo_v, [mi_v, mt_len]))
        ctx.emit(IRInstr("br.t", None, [mgo_v, mb_b.label, me_b.label]))
        ctx.switch_to(mb_b)
        mbi_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", mbi_v, [mt_idx]))
        mk_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", mk_v, ["_abi_str_char_at", frm_v, mbi_v]))
        mv_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", mv_v, ["_abi_str_char_at", to_v, mbi_v]))
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", tbl_v, mk_v, mv_v]))
        m1_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", m1_v, [1]))
        mn_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", mn_v, [mbi_v, m1_v]))
        ctx.emit(IRInstr("store", None, [mn_v, mt_idx]))
        ctx.emit(IRInstr("br", None, [mh_b.label]))
        ctx.switch_to(me_b)
        if len(e.args) == 3:
            del_v = _lower_expr(ctx, e.args[2])
            empty_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", empty_v, [ctx.mctx.intern_str("")]))
            dl_len = ctx.tmp(I64)
            ctx.emit(IRInstr("call", dl_len, ["strlen", del_v]))
            dl_idx = ctx.ensure_slot(f"__mktrd_i_{id(e)}", I64)
            dz_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", dz_v, [0]))
            ctx.emit(IRInstr("store", None, [dz_v, dl_idx]))
            dh_b = ctx.new_block("mktrdhead")
            db_b = ctx.new_block("mktrdbody")
            de_b = ctx.new_block("mktrdend")
            ctx.emit(IRInstr("br", None, [dh_b.label]))
            ctx.switch_to(dh_b)
            di_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", di_v, [dl_idx]))
            dgo_v = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.lt", dgo_v, [di_v, dl_len]))
            ctx.emit(IRInstr("br.t", None, [dgo_v, db_b.label, de_b.label]))
            ctx.switch_to(db_b)
            dbi_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", dbi_v, [dl_idx]))
            dk_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", dk_v, ["_abi_str_char_at", del_v, dbi_v]))
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", tbl_v, dk_v, empty_v]))
            d1_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", d1_v, [1]))
            dn_v = ctx.tmp(I64)
            ctx.emit(IRInstr("iadd", dn_v, [dbi_v, d1_v]))
            ctx.emit(IRInstr("store", None, [dn_v, dl_idx]))
            ctx.emit(IRInstr("br", None, [dh_b.label]))
            ctx.switch_to(de_b)
        return tbl_v

    if (
        isinstance(e, A.MethodCall)
        and isinstance(e.obj, A.Name)
        and e.obj.name == "dict"
        and e.method == "fromkeys"
        and 1 <= len(e.args) <= 2
    ):
        # `dict.fromkeys(keys[, value])`: fresh dict, every key mapped to the
        # same value (0 when omitted, matching CPython's None closely enough
        # for asmpython's int-default convention).
        keys_v = _lower_expr(ctx, e.args[0])
        if len(e.args) == 2:
            fill_v = _lower_for_slot(ctx, e.args[1], A.expr_type(e.args[1]))
        else:
            fill_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", fill_v, [0]))
        out_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", out_v, ["_abi_new_instance"]))
        fk_len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", fk_len_addr, [keys_v, _LIST_LEN_OFF]))
        fk_len = ctx.tmp(I64)
        ctx.emit(IRInstr("load", fk_len, [fk_len_addr]))
        fk_idx = ctx.ensure_slot(f"__fromkeys_idx_{id(e)}", I64)
        fk_z = ctx.tmp(I64)
        ctx.emit(IRInstr("const", fk_z, [0]))
        ctx.emit(IRInstr("store", None, [fk_z, fk_idx]))
        fh_b = ctx.new_block("fromkeyshead")
        fb_b = ctx.new_block("fromkeysbody")
        fe_b = ctx.new_block("fromkeysend")
        ctx.emit(IRInstr("br", None, [fh_b.label]))
        ctx.switch_to(fh_b)
        fi_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", fi_v, [fk_idx]))
        fgo_v = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", fgo_v, [fi_v, fk_len]))
        ctx.emit(IRInstr("br.t", None, [fgo_v, fb_b.label, fe_b.label]))
        ctx.switch_to(fb_b)
        fbi_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", fbi_v, [fk_idx]))
        fk_addr = _list_elem_addr(ctx, keys_v, fbi_v)
        fkey_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", fkey_v, [fk_addr]))
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", out_v, fkey_v, fill_v]))
        f1_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", f1_v, [1]))
        fn_v = ctx.tmp(I64)
        ctx.emit(IRInstr("iadd", fn_v, [fbi_v, f1_v]))
        ctx.emit(IRInstr("store", None, [fn_v, fk_idx]))
        ctx.emit(IRInstr("br", None, [fh_b.label]))
        ctx.switch_to(fe_b)
        return out_v

    if isinstance(e, A.MethodCall):
        if getattr(e, "field_callable", False):
            # `obj.fn(args)` where `fn` is a FIELD holding a callable, not a
            # method (sema resolved that -- see `_try_field_callable_call`).
            # Read the field, then call through it: the same indirect call the
            # `callable_indirect` A.Call path emits, just with the callee
            # spelled as an attribute read.
            fn_v = _lower_expr(ctx, A.Attr(obj=e.obj, name=e.method, pos=e.pos))
            fc_args = [_lower_expr(ctx, a) for a in e.args]
            fc_ty = ir_type_for(A.expr_type(e))
            fv = ctx.tmp(fc_ty)
            ctx.emit(IRInstr("call", fv, [fn_v, *fc_args]))
            return fv
        obj_ty = A.expr_type(e.obj)
        if obj_ty == "float" and e.method == "is_integer" and not e.args:
            # `x.is_integer()`: true when the value equals its own truncation.
            # `floor` would be wrong for a negative fraction; truncation toward
            # zero is exactly the identity CPython tests.
            _fv = _lower_expr(ctx, e.obj)
            _ti = ctx.tmp(I64)
            ctx.emit(IRInstr("fptosi", _ti, [_fv]))
            _tf = ctx.tmp(F64)
            ctx.emit(IRInstr("sitofp", _tf, [_ti]))
            _r = ctx.tmp(I64)
            ctx.emit(IRInstr("fcmp.eq", _r, [_fv, _tf]))
            return _r
        if obj_ty == "int" and e.method == "to_bytes":
            return _lower_int_to_bytes(ctx, e)
        if obj_ty == "int" and e.method in ("bit_length", "bit_count") and not e.args:
            # bit_length(): how many bits the value needs -- shift right until
            # it's zero, counting steps. bit_count(): population count -- same
            # walk, accumulating the low bit instead. Both are plain loops
            # rather than a runtime helper (no BSR/POPCNT IR op exists, and
            # this keeps them backend-independent).
            src_v = _lower_expr(ctx, e.obj)
            val_ptr = ctx.ensure_slot(f"__bitv_{id(e)}", I64)
            acc_ptr = ctx.ensure_slot(f"__bitn_{id(e)}", I64)
            ctx.emit(IRInstr("store", None, [src_v, val_ptr]))
            zc = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zc, [0]))
            ctx.emit(IRInstr("store", None, [zc, acc_ptr]))
            bh_b = ctx.new_block("bithead")
            bb_b = ctx.new_block("bitbody")
            be_b = ctx.new_block("bitend")
            ctx.emit(IRInstr("br", None, [bh_b.label]))
            ctx.switch_to(bh_b)
            cur_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", cur_v, [val_ptr]))
            zc2 = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zc2, [0]))
            more_v = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.ne", more_v, [cur_v, zc2]))
            ctx.emit(IRInstr("br.t", None, [more_v, bb_b.label, be_b.label]))
            ctx.switch_to(bb_b)
            bv = ctx.tmp(I64)
            ctx.emit(IRInstr("load", bv, [val_ptr]))
            acc = ctx.tmp(I64)
            ctx.emit(IRInstr("load", acc, [acc_ptr]))
            one_c = ctx.tmp(I64)
            ctx.emit(IRInstr("const", one_c, [1]))
            if e.method == "bit_count":
                lowbit = ctx.tmp(I64)
                ctx.emit(IRInstr("iand", lowbit, [bv, one_c]))
                step = lowbit
            else:
                step = one_c
            nacc = ctx.tmp(I64)
            ctx.emit(IRInstr("iadd", nacc, [acc, step]))
            ctx.emit(IRInstr("store", None, [nacc, acc_ptr]))
            shifted = ctx.tmp(I64)
            ctx.emit(IRInstr("shr", shifted, [bv, one_c]))
            ctx.emit(IRInstr("store", None, [shifted, val_ptr]))
            ctx.emit(IRInstr("br", None, [bh_b.label]))
            ctx.switch_to(be_b)
            out_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", out_v, [acc_ptr]))
            return out_v
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
            if e.obj.name == "os" and e.method == "listdir" and len(e.args) <= 1:
                return _lower_os_listdir(ctx, e.args[0] if e.args else None)
            # The random.* special cases below match on the resolved
            # BINDING's own `c_name` (`_random_randint` etc.), not on
            # `e.obj.name == "random"` -- a module can be imported under
            # any local alias (`import random as _random`, as
            # secrets.py itself does), and `e.obj.name` is that LOCAL
            # alias, not the real module identity. Matching on the
            # alias string missed every aliased-import call site
            # entirely (confirmed via `213_secrets_module.py`, whose
            # own `_random.randint(...)` calls fell through to the
            # generic FFI dispatch below, unresolved).
            _random_fn = ctx.mctx.imported_modules[e.obj.name].get(e.method)
            _random_c_name = getattr(_random_fn, "c_name", None)
            if _random_c_name == "_random_randint" and len(e.args) == 2:
                # `random.randint(a, b) -> a + rand() % (b - a + 1)`.
                # Bound to a `c_name="_random_randint"` Func entry in
                # random.py's own BINDINGS, but that symbol was never a
                # real C function anywhere -- random.py's own docstring
                # says it's meant to be "implemented as inline NASM
                # helpers in the target subclasses", and codegen.py's
                # target_windows.py DOES have exactly that (`label
                # "_random_randint"`, generated only into the output
                # binary when actually called) -- this backend had no
                # equivalent at all. Ported the identical formula
                # directly as IR ops instead of a hand-written asm
                # label, matching codegen.py's own algorithm exactly
                # (confirmed via the legacy backend producing the same
                # `random.seed(42); randint(1,10)` sequence this test's
                # `# expect:` block was written against).
                a_v = _lower_expr(ctx, e.args[0])
                b_v = _lower_expr(ctx, e.args[1])
                r_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", r_v, ["rand"]))
                range_v = ctx.tmp(I64)
                ctx.emit(IRInstr("isub", range_v, [b_v, a_v]))
                one_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one_v, [1]))
                span_v = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", span_v, [range_v, one_v]))
                rem_v = ctx.tmp(I64)
                ctx.emit(IRInstr("irem", rem_v, [r_v, span_v]))
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", out, [rem_v, a_v]))
                return out
            if _random_c_name == "_random_random" and not e.args:
                # `random.random() -> rand() / 32768.0`, matching
                # codegen.py's `_random_random`/`_rand_inv` constant.
                r_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", r_v, ["rand"]))
                r_f = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", r_f, [r_v]))
                inv_v = ctx.tmp(F64)
                ctx.emit(IRInstr("const", inv_v, [1.0 / 32768.0]))
                out = ctx.tmp(F64)
                ctx.emit(IRInstr("fmul", out, [r_f, inv_v]))
                return out
            if _random_c_name == "_random_randrange" and len(e.args) == 1:
                # `random.randrange(stop) -> rand() % stop`.
                stop_v = _lower_expr(ctx, e.args[0])
                r_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", r_v, ["rand"]))
                out = ctx.tmp(I64)
                ctx.emit(IRInstr("irem", out, [r_v, stop_v]))
                return out
            if _random_c_name == "_random_uniform" and len(e.args) == 2:
                # `random.uniform(a, b) -> a + (rand()/32768.0) * (b - a)`,
                # matching codegen.py's `_random_uniform` and the shared
                # `_rand_inv = 1.0/32768` constant it uses.
                a_v = _lower_expr(ctx, e.args[0])
                b_v = _lower_expr(ctx, e.args[1])
                r_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", r_v, ["rand"]))
                r_f = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", r_f, [r_v]))
                inv_v = ctx.tmp(F64)
                ctx.emit(IRInstr("const", inv_v, [1.0 / 32768.0]))
                frac_v = ctx.tmp(F64)
                ctx.emit(IRInstr("fmul", frac_v, [r_f, inv_v]))
                span_v = ctx.tmp(F64)
                ctx.emit(IRInstr("fsub", span_v, [b_v, a_v]))
                scaled_v = ctx.tmp(F64)
                ctx.emit(IRInstr("fmul", scaled_v, [frac_v, span_v]))
                out = ctx.tmp(F64)
                ctx.emit(IRInstr("fadd", out, [scaled_v, a_v]))
                return out
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
                    if i < len(arg_types) and arg_types[i] == "list_buf":
                        # `os._stat(path, buf)`-style bindings pass a
                        # `list[int]`'s underlying DATA BUFFER (not its
                        # 24-byte header) as a raw out-parameter pointer,
                        # matching codegen.py's own `list_buf` handling
                        # exactly (see its comment on `_gen_ffi_call`).
                        # Without this, `av` was still the list's HEADER
                        # pointer -- the C function wrote its struct
                        # fields into the header's cap/len/buf_ptr words
                        # instead of the real backing array, corrupting
                        # the list's own bookkeeping (confirmed via
                        # `ospath.isdir`/`isfile`: `os._stat`'s writes
                        # scrambled the buffer list's length/capacity,
                        # later crashing on any read of it).
                        buf_v = ctx.tmp(PTR)
                        ctx.emit(IRInstr("gep", buf_v, [av, _LIST_BUF_OFF]))
                        buf_ptr_v = ctx.tmp(PTR)
                        ctx.emit(IRInstr("load", buf_ptr_v, [buf_v]))
                        av = buf_ptr_v
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
                if c_name == "erf" and len(args) == 1:
                    # erf isn't a real msvcrt.dll export (confirmed via
                    # ctypes.WinDLL('msvcrt.dll') attribute lookup) -- the
                    # x86-64 backend's from-scratch PE linker has no static
                    # libm to pull it from (unlike the legacy gcc-linked
                    # backend, which mingw supplies it for free), so route
                    # to a native computed shim in abi_shims.asm instead of
                    # an unresolvable extern. See _math_erf there for the
                    # Abramowitz & Stegun 7.1.26 polynomial approximation.
                    v = ctx.tmp(F64)
                    ctx.emit(IRInstr("call", v, ["_math_erf", args[0]]))
                    return v
                if c_name == "tgamma" and len(args) == 1:
                    # tgamma has the same problem as erf: not a real
                    # msvcrt.dll export. Route to a native Lanczos (g=7,
                    # n=9) approximation shim (_math_gamma in abi_shims.asm)
                    # instead of an unresolvable extern.
                    v = ctx.tmp(F64)
                    ctx.emit(IRInstr("call", v, ["_math_gamma", args[0]]))
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
                if ret_ty == "int" and ret_conv != "ptr":
                    # A real C `int` is 32-bit -- the callee returns it in
                    # EAX with the upper 32 bits of RAX left UNSPECIFIED
                    # by the calling convention, but every asmpython
                    # value is a full 64-bit slot. Without sign-
                    # extending, a result like `fgetc()`'s EOF sentinel
                    # (`-1`) read back as `0x00000000FFFFFFFF`
                    # (4294967295) instead of `-1`, so `while c != -1:`
                    # never terminated -- confirmed via
                    # `93_os_file_io.py`/`255_os_file_io.py` both timing
                    # out in the sweep instead of crashing (same
                    # "genuinely infinite, not a crash" shape as the
                    # earlier `__bool__`/`__len__` truthiness bug).
                    # codegen.py's `_gen_ffi_call` has always had this
                    # exact `movsxd rax, eax` fix (see its own comment);
                    # this backend's FFI call-return path had no
                    # equivalent at all. `ret_conv == "ptr"` is excluded
                    # since that flag means the C function genuinely
                    # returns a real 64-bit pointer/handle in RAX (e.g.
                    # SDL_CreateWindow) -- sign-extending just EAX would
                    # truncate it to 32 bits.
                    ext = ctx.tmp(I64)
                    ctx.emit(IRInstr("sext", ext, [IRValue(v.name, IRType("i32"))]))
                    v = ext
                return v
        if obj_ty == "list":
            if e.method == "append" and len(e.args) == 1:
                obj_v = _lower_expr(ctx, e.obj)
                if getattr(e, "box_element", False):
                    # `xs.append(value)` on an EXPLICIT `list[object]` (sema
                    # stamped box_element): route through the store choke
                    # point so a concrete scalar is boxed AND an already-"any"
                    # value (a boxed cell forwarded from a `v: object` param)
                    # stays boxed -- so `type(xs[i])`/`isinstance(xs[i], int)`
                    # on the read-out element can answer. Previously gated on
                    # a concrete-scalar value, so a forwarded boxed value took
                    # the else branch and was unboxed on the way in.
                    val = _lower_value_into_any_slot(ctx, e.args[0])
                else:
                    val = _lower_expr(ctx, e.args[0])
                    if A.expr_type(e.args[0]) == "float":
                        # A list cell is a raw 8-byte int slot -- bitcast the
                        # double's bits into an I64 so the runtime append
                        # helper (which just copies 8 raw bytes) doesn't need
                        # to know or care it's really a float. Mirrors
                        # codegen.py's `movq rax, xmm0` before its own
                        # _runtime_list_append call exactly.
                        iv = ctx.tmp(I64)
                        ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
                        val = iv
                ctx.emit(IRInstr("call", None, ["_abi_list_append", obj_v, val]))
                return ctx.shared_zero  # list.append() returns None
            if e.method == "insert" and len(e.args) == 2:
                obj_v = _lower_expr(ctx, e.obj)
                idx_v = _lower_expr(ctx, e.args[0])
                val_v = _lower_expr(ctx, e.args[1])
                if A.expr_type(e.args[1]) == "float":
                    iv = ctx.tmp(I64)
                    ctx.emit(IRInstr("bitcast_f2i", iv, [val_v]))
                    val_v = iv
                ctx.emit(IRInstr("call", None, ["_abi_list_insert", obj_v, idx_v, val_v]))
                return ctx.shared_zero
            if e.method == "pop" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                if A.expr_type(e) == "float":
                    # Reverse of append's bitcast: the popped 8-byte
                    # cell holds a double's raw bits, read back as I64
                    # by _abi_list_pop -- bitcast to F64 before use.
                    # Mirrors codegen.py's `movq xmm0, rax` after its
                    # own _runtime_list_pop call.
                    iv = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", iv, ["_abi_list_pop", obj_v]))
                    fv = ctx.tmp(F64)
                    ctx.emit(IRInstr("bitcast_i2f", fv, [iv]))
                    return fv
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
            if e.method == "extend" and len(e.args) == 1 and A.expr_type(e.args[0]) in ("list", "tuple", "set", "any"):
                # A tuple/set shares the list buffer layout, so _abi_list_extend
                # walks it exactly like a list (sema already accepts these). An
                # "any" argument that is a list/tuple/set at runtime works
                # identically -- `_lower_expr` unboxes it to the raw
                # list-shaped pointer, which is exactly what the helper walks;
                # a non-sequence "any" would be a runtime type error in real
                # Python too, so accepting it here matches CPython's own
                # deferral of the check to runtime.
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
                tuple_key_kind = (
                    getattr(e.obj, "list_el_value_type", "int") or "int"
                )
                _lower_sort_inplace(
                    ctx,
                    e,
                    obj_v,
                    el_kind,
                    tuple_key_kind=tuple_key_kind,
                )
                return ctx.shared_zero  # list.sort() returns None
            raise LowerError(f"unsupported expr MethodCall (list.{e.method})")
        if obj_ty == "dict":
            if e.method == "get" and len(e.args) in (1, 2):
                obj_v = _lower_expr(ctx, e.obj)
                key_v = _lower_dict_key(ctx, e.args[0])
                res_ty = A.expr_type(e)
                res_is_float = res_ty == "float"
                # An `any`-valued dict boxes its stored values, so `get` must
                # return a PTR box for the read choke to unbox (same fix as the
                # `d[k]` subscript path). Box the DEFAULT too so the key-absent
                # result is a box as well -- otherwise a raw default would be
                # unboxed as if it were a box pointer. (sema keeps the result
                # "any" for int/str defaults precisely so this fires.)
                any_val = res_ty == "any"
                if len(e.args) == 2:
                    if any_val:
                        default_v = _lower_value_into_any_slot(ctx, e.args[1])
                    else:
                        default_v = _lower_expr(ctx, e.args[1])
                        if res_is_float and A.expr_type(e.args[1]) == "float":
                            dv = ctx.tmp(I64)
                            ctx.emit(IRInstr("bitcast_f2i", dv, [default_v]))
                            default_v = dv
                elif any_val:
                    default_v = _lower_value_into_any_slot(
                        ctx, A.IntLit(value=0, pos=e.pos)
                    )
                else:
                    default_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", default_v, [0]))
                v = ctx.tmp(PTR if any_val else I64)
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
            if e.method == "contains" and len(e.args) == 1:
                # `d.contains(x)` -- an asmpython convenience alias for
                # `x in d` (used directly as a method in several test
                # cases, e.g. `19_dicts.py`). `_abi_dict_contains` is
                # already the exact shim `x in d`'s own Compare-membership
                # lowering calls; was simply never wired up as a
                # MethodCall too.
                obj_v = _lower_expr(ctx, e.obj)
                key_v = _lower_dict_key(ctx, e.args[0])
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, ["_abi_dict_contains", obj_v, key_v]))
                return v
            if e.method == "keys" and not e.args:
                # `d.keys()` alone (not chained into a for-loop, which
                # has its own separate dict-keys iteration path) -- just
                # the plain list-of-keys value. `_abi_dict_keys` already
                # exists and is used by several other call sites
                # (items(), for-in-dict, etc.); this MethodCall shape
                # alone was never wired up.
                obj_v = _lower_expr(ctx, e.obj)
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_dict_keys", obj_v]))
                return v
            if e.method == "values" and not e.args:
                obj_v = _lower_expr(ctx, e.obj)
                keys_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", keys_v, ["_abi_dict_keys", obj_v]))
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [keys_v, _LIST_LEN_OFF]))
                len_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", len_v, [len_addr]))
                out_v = _new_list_from_len(ctx, len_v)
                out_ptr = ctx.ensure_slot(f"__dictvalues_out_{id(e)}", PTR)
                keys_ptr = ctx.ensure_slot(f"__dictvalues_keys_{id(e)}", PTR)
                idx_ptr = ctx.ensure_slot(f"__dictvalues_idx_{id(e)}", I64)
                dict_ptr = ctx.ensure_slot(f"__dictvalues_dict_{id(e)}", PTR)
                ctx.emit(IRInstr("store", None, [out_v, out_ptr]))
                ctx.emit(IRInstr("store", None, [keys_v, keys_ptr]))
                ctx.emit(IRInstr("store", None, [obj_v, dict_ptr]))
                zero_i = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero_i, [0]))
                ctx.emit(IRInstr("store", None, [zero_i, idx_ptr]))

                head_b = ctx.new_block("dictvalueshead")
                body_b = ctx.new_block("dictvaluesbody")
                end_b = ctx.new_block("dictvaluesend")
                ctx.emit(IRInstr("br", None, [head_b.label]))
                ctx.switch_to(head_b)
                idx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
                cond_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
                ctx.emit(IRInstr("br.t", None, [cond_v, body_b.label, end_b.label]))

                ctx.switch_to(body_b)
                idx_v2 = ctx.tmp(I64)
                ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
                keys_v2 = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", keys_v2, [keys_ptr]))
                key_addr = _list_elem_addr(ctx, keys_v2, idx_v2)
                key_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", key_v, [key_addr]))
                dict_v2 = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", dict_v2, [dict_ptr]))
                zero_default = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero_default, [0]))
                value_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", value_v, ["_abi_dict_get_default", dict_v2, key_v, zero_default]))
                out_v2 = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", out_v2, [out_ptr]))
                ctx.emit(IRInstr("call", None, ["_abi_list_append", out_v2, value_v]))
                one_i = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one_i, [1]))
                next_idx = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_i]))
                ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
                ctx.emit(IRInstr("br", None, [head_b.label]))

                ctx.switch_to(end_b)
                final_out = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", final_out, [out_ptr]))
                return final_out
            if e.method == "popitem" and not e.args:
                # d.popitem() -> remove and return a (key, value) pair. CPython
                # pops the LAST inserted entry, which is the last key
                # `_abi_dict_keys` reports (insertion order is preserved).
                # The value moves as raw bits for the same reason items() does:
                # the pair cell is a raw 8-byte slot and its kind is static.
                obj_v = _lower_expr(ctx, e.obj)
                pk_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", pk_v, ["_abi_dict_keys", obj_v]))
                pl_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", pl_addr, [pk_v, _LIST_LEN_OFF]))
                pl_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", pl_v, [pl_addr]))
                p1_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", p1_v, [1]))
                plast_v = ctx.tmp(I64)
                ctx.emit(IRInstr("isub", plast_v, [pl_v, p1_v]))
                pkaddr = _list_elem_addr(ctx, pk_v, plast_v)
                pkey_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", pkey_v, [pkaddr]))
                pz_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", pz_v, [0]))
                pval_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", pval_v, ["_abi_dict_get_default", obj_v, pkey_v, pz_v]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_pop", obj_v, pkey_v]))
                pcap_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", pcap_v, [2]))
                ppair_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", ppair_v, ["_abi_new_list", pcap_v]))
                ctx.emit(IRInstr("call", None, ["_abi_list_append", ppair_v, pkey_v]))
                ctx.emit(IRInstr("call", None, ["_abi_list_append", ppair_v, pval_v]))
                return ppair_v
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
                # The pair's value cell is a raw 8-byte slot, and the consumer
                # (target unpack / repr) already knows the slot's kind, so move
                # the value through as RAW BITS.
                #
                # A float value must not be typed F64 here: `_abi_dict_get_default`
                # returns in a GP register, so an F64-typed result makes the
                # backend read XMM0 instead -- the same ABI mismatch this file
                # documents around its other float/GP boundaries -- and
                # `_abi_list_append` would then need a bitcast anyway. The
                # symptom was a float-valued dict's items() pairs holding a
                # POINTER-shaped denormal (`{'a': 1.5}.items()` reprs as
                # `[('a', 2.9e-317)]`) while `d[k]` and `.values()` were both
                # correct, plus an 'XmmLoc has no attribute offset' regalloc
                # crash once a second float dict comprehension raised register
                # pressure.
                _val_ir = I64 if val_ty == "float" else ir_type_for(val_ty)
                default_v = ctx.tmp(_val_ir)
                ctx.emit(IRInstr("const", default_v, [0]))
                val_v = ctx.tmp(_val_ir)
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
            if e.method == "copy" and not e.args:
                # d.copy() -- a shallow copy: new empty dict, then merge
                # every entry in (same semantics _abi_dict_update already
                # provides for `dict(other_dict)`/`d1 |= d2`).
                obj_v = _lower_expr(ctx, e.obj)
                new_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", new_v, ["_abi_new_instance"]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_update", new_v, obj_v]))
                return new_v
            if e.method == "clear" and not e.args:
                # _abi_dict_clear already exists as a runtime shim -- was
                # simply never wired up as a MethodCall dispatch target.
                obj_v = _lower_expr(ctx, e.obj)
                ctx.emit(IRInstr("call", None, ["_abi_dict_clear", obj_v]))
                return ctx.shared_zero
            if e.method == "setdefault" and len(e.args) == 2:
                # d.setdefault(key, default): key present -> return its
                # existing value unchanged; absent -> insert default and
                # return it. Built from the same contains-check-then-
                # branch shape dict.pop()'s two-arg form already uses.
                obj_v = _lower_expr(ctx, e.obj)
                key_v = _lower_dict_key(ctx, e.args[0])
                res_ty = ir_type_for(A.expr_type(e))
                has_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", has_v, ["_abi_dict_contains", obj_v, key_v]))
                present_b = ctx.new_block("dictsetdefpresent")
                missing_b = ctx.new_block("dictsetdefmissing")
                end_b = ctx.new_block("dictsetdefend")
                res_ptr = ctx.ensure_slot(f"__dictsetdef_res_{id(e)}", res_ty)
                ctx.emit(IRInstr("br.t", None, [has_v, present_b.label, missing_b.label]))

                ctx.switch_to(missing_b)
                # The default is stored INTO the dict, so route it through the
                # store choke point with the dict's OWN value-slot kind (from
                # `e.obj.value_type`, the same source the IndexAssign store
                # uses -- NOT `A.expr_type(e)`, which is the setdefault call's
                # result type and can differ in a narrowed/match context): an
                # object-valued dict boxes a scalar so a later `d[k]` read
                # re-unboxes the same cell.
                _sd_vt = getattr(e.obj, "value_type", "int")
                default_v = _lower_for_slot(ctx, e.args[1], _sd_vt)
                ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_v, default_v]))
                ctx.emit(IRInstr("store", None, [default_v, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

                ctx.switch_to(present_b)
                zero = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero, [0]))
                got_v = ctx.tmp(res_ty)
                ctx.emit(IRInstr("call", got_v, ["_abi_dict_get_default", obj_v, key_v, zero]))
                ctx.emit(IRInstr("store", None, [got_v, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

                ctx.switch_to(end_b)
                out = ctx.tmp(res_ty)
                ctx.emit(IRInstr("load", out, [res_ptr]))
                return out
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
            if e.method == "update" and len(e.args) == 1:
                obj_v0 = _lower_expr(ctx, e.obj)
                other_v0 = _lower_expr(ctx, e.args[0])
                other_ty = A.expr_type(e.args[0])
                if other_ty in ("list", "tuple"):
                    # Sets are dict-backed, but list/tuple headers are not
                    # dict-shaped. Passing one to _abi_dict_update makes the
                    # runtime read the list's unallocated +0x20 field as a
                    # dict order buffer. Iterate sequence inputs instead.
                    obj_ptr = ctx.ensure_slot(f"__setupd_obj_{id(e)}", PTR)
                    ctx.emit(IRInstr("store", None, [obj_v0, obj_ptr]))
                    other_ptr = ctx.ensure_slot(f"__setupd_src_{id(e)}", PTR)
                    ctx.emit(IRInstr("store", None, [other_v0, other_ptr]))
                    len_addr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", len_addr, [other_v0, _LIST_LEN_OFF]))
                    len_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", len_v, [len_addr]))
                    idx_ptr = ctx.ensure_slot(f"__setupd_idx_{id(e)}", I64)
                    zero_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", zero_v, [0]))
                    ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))
                    el_ty = _iter_element_type(e.args[0])

                    head_b = ctx.new_block("setupdhead")
                    body_b = ctx.new_block("setupdbody")
                    end_b = ctx.new_block("setupdend")
                    ctx.emit(IRInstr("br", None, [head_b.label]))

                    ctx.switch_to(head_b)
                    idx_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
                    cond_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.lt", cond_v, [idx_v, len_v]))
                    ctx.emit(
                        IRInstr(
                            "br.t",
                            None,
                            [cond_v, body_b.label, end_b.label],
                        )
                    )

                    ctx.switch_to(body_b)
                    idx_v2 = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", idx_v2, [idx_ptr]))
                    other_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("load", other_v, [other_ptr]))
                    addr = _list_elem_addr(ctx, other_v, idx_v2)
                    elem_v = ctx.tmp(ir_type_for(el_ty))
                    ctx.emit(IRInstr("load", elem_v, [addr]))
                    if el_ty == "int":
                        base10 = ctx.tmp(I64)
                        ctx.emit(IRInstr("const", base10, [10]))
                        empty_name = ctx.mctx.intern_str("")
                        empty_v = ctx.tmp(PTR)
                        ctx.emit(IRInstr("global_addr", empty_v, [empty_name]))
                        key_v = ctx.tmp(PTR)
                        ctx.emit(
                            IRInstr(
                                "call",
                                key_v,
                                ["_abi_int_to_base", elem_v, base10, empty_v],
                            )
                        )
                    else:
                        key_v = elem_v
                    obj_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("load", obj_v, [obj_ptr]))
                    one_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", one_v, [1]))
                    ctx.emit(
                        IRInstr(
                            "call",
                            None,
                            ["_abi_dict_set", obj_v, key_v, one_v],
                        )
                    )
                    next_idx = ctx.tmp(I64)
                    ctx.emit(IRInstr("iadd", next_idx, [idx_v2, one_v]))
                    ctx.emit(IRInstr("store", None, [next_idx, idx_ptr]))
                    ctx.emit(IRInstr("br", None, [head_b.label]))

                    ctx.switch_to(end_b)
                else:
                    ctx.emit(
                        IRInstr(
                            "call",
                            None,
                            ["_abi_dict_update", obj_v0, other_v0],
                        )
                    )
                return ctx.shared_zero
            if e.method in ("union", "intersection", "difference") and len(e.args) == 1:
                return _lower_set_setop(ctx, e.obj, e.args[0], e.method, id(e))
            if (
                e.method in (
                    "isdisjoint", "issuperset",
                    "intersection_update", "difference_update",
                )
                and len(e.args) == 1
            ):
                return _lower_set_pairop(ctx, e.obj, e.args[0], e.method, id(e))
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
                msg_name = ctx.mctx.intern_str("'pop from an empty set'")
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
        if obj_ty == "str" and e.method == "format" and isinstance(e.obj, A.StrLit):
            return _lower_str_format(ctx, e)
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
                "count": "_abi_str_count",
                "startswith": "_abi_str_starts_with", "endswith": "_abi_str_ends_with",
            }
            # find/index/rfind/rindex: both 1-arg (no start) and 2-arg
            # (sub, start) forms, plus index()/rindex() raise ValueError
            # on a miss instead of returning -1 (find()/rfind() don't).
            # Previously only find()'s 1-arg form was wired (via the
            # generic one_arg_int_methods table above); rfind/index/
            # rindex weren't handled at all, and none of the four
            # supported a start position -- both real gaps, not just a
            # missing DLL registration. Mirrors codegen.py's own
            # _runtime_str_index_of/_rindex_of/_index_of_start dispatch
            # and its index()/rindex() not-found ValueError check.
            if e.method in ("find", "index", "rfind", "rindex") and len(e.args) in (1, 2):
                if A.expr_type(e.args[0]) != "str":
                    raise LowerError(f"unsupported expr MethodCall (str.{e.method} non-str arg)")
                sub_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(I64)
                if len(e.args) == 2:
                    start_v = _lower_expr(ctx, e.args[1])
                    if e.method in ("find", "index"):
                        ctx.emit(IRInstr("call", v, ["_abi_str_index_of_start", obj_v, sub_v, start_v]))
                    else:
                        raise LowerError(f"unsupported expr MethodCall (str.{e.method} with start)")
                else:
                    fn = "_abi_str_index_of" if e.method in ("find", "index") else "_abi_str_rindex_of"
                    ctx.emit(IRInstr("call", v, [fn, obj_v, sub_v]))
                if e.method in ("index", "rindex"):
                    _emit_str_index_check(ctx, v, id(e))
                return v
            if e.method == "expandtabs" and len(e.args) in (0, 1):
                # str.expandtabs([tabsize=8]) -- the Python-level default
                # is applied here (matching codegen.py's own `mov rbx, 8`
                # for the 0-arg form); _abi_str_expandtabs always takes
                # an explicit tabsize.
                if e.args:
                    tabsize_v = _lower_expr(ctx, e.args[0])
                else:
                    tabsize_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", tabsize_v, [8]))
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_expandtabs", obj_v, tabsize_v]))
                return v
            one_arg_str_methods = {
                "zfill": "_abi_str_zfill", "removeprefix": "_abi_str_removeprefix",
                "removesuffix": "_abi_str_removesuffix",
            }
            pad_methods = {
                "ljust": "_abi_str_ljust",
                "rjust": "_abi_str_rjust",
                "center": "_abi_str_center",
            }
            if e.method in ("isnumeric", "isprintable", "isidentifier") and not e.args:
                # Per-character classification the runtime has no helper for.
                # Scan the bytes directly (`gep`+U8 `load`, the same addressing
                # ord() uses) and clear a result flag on the first character
                # that fails the test. ASCII ranges only -- the same
                # simplification the rest of this file's str machinery makes.
                kind = e.method
                slen_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", slen_v, ["strlen", obj_v]))
                res_ptr = ctx.ensure_slot(f"__spred_res_{id(e)}", I64)
                idx_ptr = ctx.ensure_slot(f"__spred_idx_{id(e)}", I64)
                one_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one_v, [1]))
                ctx.emit(IRInstr("store", None, [one_v, res_ptr]))
                zero_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero_v, [0]))
                ctx.emit(IRInstr("store", None, [zero_v, idx_ptr]))
                if kind in ("isnumeric", "isidentifier"):
                    # "" is False for these two (but True for isprintable).
                    empty_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.eq", empty_v, [slen_v, zero_v]))
                    emp_b = ctx.new_block("spredempty")
                    non_b = ctx.new_block("sprednonempty")
                    ctx.emit(IRInstr("br.t", None, [empty_v, emp_b.label, non_b.label]))
                    ctx.switch_to(emp_b)
                    ctx.emit(IRInstr("store", None, [zero_v, res_ptr]))
                    ctx.emit(IRInstr("br", None, [non_b.label]))
                    ctx.switch_to(non_b)
                ph_b = ctx.new_block("spredhead")
                pb_b = ctx.new_block("spredbody")
                pn_b = ctx.new_block("sprednext")
                pf_b = ctx.new_block("spredfail")
                pe_b = ctx.new_block("spredend")
                ctx.emit(IRInstr("br", None, [ph_b.label]))
                ctx.switch_to(ph_b)
                pi_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", pi_v, [idx_ptr]))
                pgo_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", pgo_v, [pi_v, slen_v]))
                ctx.emit(IRInstr("br.t", None, [pgo_v, pb_b.label, pe_b.label]))
                ctx.switch_to(pb_b)
                pbi_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", pbi_v, [idx_ptr]))
                caddr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", caddr, [obj_v, pbi_v]))
                cb_v = ctx.tmp(U8)
                ctx.emit(IRInstr("load", cb_v, [caddr]))
                c_v = ctx.tmp(I64)
                ctx.emit(IRInstr("zext", c_v, [cb_v]))

                def _in_range(lo: int, hi: int) -> IRValue:
                    lo_c = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", lo_c, [lo]))
                    hi_c = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", hi_c, [hi]))
                    ge_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.ge", ge_v, [c_v, lo_c]))
                    le_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.le", le_v, [c_v, hi_c]))
                    both = ctx.tmp(I64)
                    ctx.emit(IRInstr("iand", both, [ge_v, le_v]))
                    return both

                def _any_of(vals: list) -> IRValue:
                    acc = vals[0]
                    for nxt in vals[1:]:
                        merged = ctx.tmp(I64)
                        ctx.emit(IRInstr("ior", merged, [acc, nxt]))
                        acc = merged
                    return acc

                if kind == "isnumeric":
                    ok_v = _in_range(48, 57)
                elif kind == "isprintable":
                    ok_v = _in_range(32, 126)
                else:
                    # isidentifier: letters and '_' anywhere, digits only after
                    # the first character.
                    us_c = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", us_c, [95]))
                    is_us = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.eq", is_us, [c_v, us_c]))
                    alpha_v = _any_of([_in_range(65, 90), _in_range(97, 122)])
                    digit_v = _in_range(48, 57)
                    notfirst = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.gt", notfirst, [pbi_v, zero_v]))
                    digit_ok = ctx.tmp(I64)
                    ctx.emit(IRInstr("iand", digit_ok, [digit_v, notfirst]))
                    ok_v = _any_of([is_us, alpha_v, digit_ok])
                ctx.emit(IRInstr("br.t", None, [ok_v, pn_b.label, pf_b.label]))
                ctx.switch_to(pn_b)
                pstep_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", pstep_v, [1]))
                pnx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", pnx_v, [pbi_v, pstep_v]))
                ctx.emit(IRInstr("store", None, [pnx_v, idx_ptr]))
                ctx.emit(IRInstr("br", None, [ph_b.label]))
                ctx.switch_to(pf_b)
                pz_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", pz_v, [0]))
                ctx.emit(IRInstr("store", None, [pz_v, res_ptr]))
                ctx.emit(IRInstr("br", None, [pe_b.label]))
                ctx.switch_to(pe_b)
                pout_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", pout_v, [res_ptr]))
                return pout_v
            if e.method in ("strip", "lstrip", "rstrip") and len(e.args) == 1:
                # `s.strip(chars)` / lstrip / rstrip: trim any character that
                # appears in `chars` from the relevant end(s), rather than
                # whitespace (the runtime helpers only do whitespace, which is
                # why the one-argument form was "unsupported expr MethodCall").
                # Scan inward from each end while the character is a member of
                # `chars` -- membership is `_abi_str_index_of(chars, ch) >= 0`
                # on the single-character string `_abi_str_char_at` returns --
                # then slice once between the two cursors.
                do_left = e.method in ("strip", "lstrip")
                do_right = e.method in ("strip", "rstrip")
                set_v = _lower_expr(ctx, e.args[0])
                slen_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", slen_v, ["strlen", obj_v]))
                lo_ptr = ctx.ensure_slot(f"__strip_lo_{id(e)}", I64)
                hi_ptr = ctx.ensure_slot(f"__strip_hi_{id(e)}", I64)
                zero_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", zero_v, [0]))
                ctx.emit(IRInstr("store", None, [zero_v, lo_ptr]))
                ctx.emit(IRInstr("store", None, [slen_v, hi_ptr]))
                if do_left:
                    lh_b = ctx.new_block("striplhead")
                    lb_b = ctx.new_block("striplbody")
                    le_b = ctx.new_block("striplend")
                    ctx.emit(IRInstr("br", None, [lh_b.label]))
                    ctx.switch_to(lh_b)
                    li_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", li_v, [lo_ptr]))
                    lhi_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", lhi_v, [hi_ptr]))
                    lgo_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.lt", lgo_v, [li_v, lhi_v]))
                    ctx.emit(IRInstr("br.t", None, [lgo_v, lb_b.label, le_b.label]))
                    ctx.switch_to(lb_b)
                    lbi_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", lbi_v, [lo_ptr]))
                    lch_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", lch_v, ["_abi_str_char_at", obj_v, lbi_v]))
                    lfnd_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", lfnd_v, ["_abi_str_index_of", set_v, lch_v]))
                    lz_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", lz_v, [0]))
                    lin_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.ge", lin_v, [lfnd_v, lz_v]))
                    ladv_b = ctx.new_block("stripladv")
                    ctx.emit(IRInstr("br.t", None, [lin_v, ladv_b.label, le_b.label]))
                    ctx.switch_to(ladv_b)
                    l1_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", l1_v, [1]))
                    lnx_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("iadd", lnx_v, [lbi_v, l1_v]))
                    ctx.emit(IRInstr("store", None, [lnx_v, lo_ptr]))
                    ctx.emit(IRInstr("br", None, [lh_b.label]))
                    ctx.switch_to(le_b)
                if do_right:
                    rh_b = ctx.new_block("striprhead")
                    rb_b = ctx.new_block("striprbody")
                    re_b = ctx.new_block("striprend")
                    ctx.emit(IRInstr("br", None, [rh_b.label]))
                    ctx.switch_to(rh_b)
                    rhi_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", rhi_v, [hi_ptr]))
                    rlo_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", rlo_v, [lo_ptr]))
                    rgo_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.lt", rgo_v, [rlo_v, rhi_v]))
                    ctx.emit(IRInstr("br.t", None, [rgo_v, rb_b.label, re_b.label]))
                    ctx.switch_to(rb_b)
                    rbi_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", rbi_v, [hi_ptr]))
                    r1_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", r1_v, [1]))
                    rlast_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("isub", rlast_v, [rbi_v, r1_v]))
                    rch_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", rch_v, ["_abi_str_char_at", obj_v, rlast_v]))
                    rfnd_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", rfnd_v, ["_abi_str_index_of", set_v, rch_v]))
                    rz_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", rz_v, [0]))
                    rin_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.ge", rin_v, [rfnd_v, rz_v]))
                    radv_b = ctx.new_block("stripradv")
                    ctx.emit(IRInstr("br.t", None, [rin_v, radv_b.label, re_b.label]))
                    ctx.switch_to(radv_b)
                    ctx.emit(IRInstr("store", None, [rlast_v, hi_ptr]))
                    ctx.emit(IRInstr("br", None, [rh_b.label]))
                    ctx.switch_to(re_b)
                fin_lo = ctx.tmp(I64)
                ctx.emit(IRInstr("load", fin_lo, [lo_ptr]))
                fin_hi = ctx.tmp(I64)
                ctx.emit(IRInstr("load", fin_hi, [hi_ptr]))
                out_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", out_v, ["_abi_str_slice", obj_v, fin_lo, fin_hi]))
                return out_v
            if e.method in no_arg_str_methods and not e.args:
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, [no_arg_str_methods[e.method], obj_v]))
                return v
            if e.method in no_arg_int_methods and not e.args:
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, [no_arg_int_methods[e.method], obj_v]))
                return v
            if (
                e.method in ("startswith", "endswith")
                and len(e.args) == 1
                and A.expr_type(e.args[0]) == "tuple"
            ):
                # str.startswith/endswith(tuple): True if the string matches ANY
                # candidate prefix/suffix. Iterate the tuple's slots (list
                # layout) and OR each per-candidate 0/1 result into an
                # accumulator. Handles both a literal `("a","b")` and a tuple
                # variable uniformly.
                fn = (
                    "_abi_str_starts_with"
                    if e.method == "startswith"
                    else "_abi_str_ends_with"
                )
                tup_v = _lower_expr(ctx, e.args[0])
                res_ptr = ctx.ensure_slot(f"__swtup_res_{id(e)}", I64)
                idx_ptr = ctx.ensure_slot(f"__swtup_idx_{id(e)}", I64)
                z = ctx.tmp(I64)
                ctx.emit(IRInstr("const", z, [0]))
                ctx.emit(IRInstr("store", None, [z, res_ptr]))
                ctx.emit(IRInstr("store", None, [z, idx_ptr]))
                len_addr = ctx.tmp(PTR)
                ctx.emit(IRInstr("gep", len_addr, [tup_v, _LIST_LEN_OFF]))
                tlen = ctx.tmp(I64)
                ctx.emit(IRInstr("load", tlen, [len_addr]))
                sw_head = ctx.new_block("swtuphead")
                sw_body = ctx.new_block("swtupbody")
                sw_end = ctx.new_block("swtupend")
                ctx.emit(IRInstr("br", None, [sw_head.label]))
                ctx.switch_to(sw_head)
                i_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", i_v, [idx_ptr]))
                go = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", go, [i_v, tlen]))
                ctx.emit(IRInstr("br.t", None, [go, sw_body.label, sw_end.label]))
                ctx.switch_to(sw_body)
                bi = ctx.tmp(I64)
                ctx.emit(IRInstr("load", bi, [idx_ptr]))
                ea = _list_elem_addr(ctx, tup_v, bi)
                cand = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", cand, [ea]))
                r = ctx.tmp(I64)
                ctx.emit(IRInstr("call", r, [fn, obj_v, cand]))
                cr = ctx.tmp(I64)
                ctx.emit(IRInstr("load", cr, [res_ptr]))
                nr = ctx.tmp(I64)
                ctx.emit(IRInstr("ior", nr, [cr, r]))
                ctx.emit(IRInstr("store", None, [nr, res_ptr]))
                one = ctx.tmp(I64)
                ctx.emit(IRInstr("const", one, [1]))
                ni = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", ni, [bi, one]))
                ctx.emit(IRInstr("store", None, [ni, idx_ptr]))
                ctx.emit(IRInstr("br", None, [sw_head.label]))
                ctx.switch_to(sw_end)
                sw_out = ctx.tmp(I64)
                ctx.emit(IRInstr("load", sw_out, [res_ptr]))
                return sw_out
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
                if len(e.args) == 2:
                    # maxsplit: the runtime always splits on every occurrence,
                    # so put the surplus back -- keep the first `maxsplit`
                    # pieces and re-join the rest with the separator, which is
                    # exactly what CPython's remainder is. Previously the
                    # argument was accepted and silently ignored, so
                    # 'a,b,c,d'.split(',', 2) returned 4 pieces instead of 3.
                    ms_v = _lower_expr(ctx, e.args[1])
                    plen_addr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("gep", plen_addr, [v, _LIST_LEN_OFF]))
                    plen_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("load", plen_v, [plen_addr]))
                    keep_v = ctx.tmp(I64)
                    one_c = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", one_c, [1]))
                    ctx.emit(IRInstr("iadd", keep_v, [ms_v, one_c]))
                    res_ptr = ctx.ensure_slot(f"__splitms_{id(e)}", PTR)
                    ctx.emit(IRInstr("store", None, [v, res_ptr]))
                    zc_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", zc_v, [0]))
                    neg_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.lt", neg_v, [ms_v, zc_v]))
                    over_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.gt", over_v, [plen_v, keep_v]))
                    trim_b = ctx.new_block("splitmstrim")
                    done_b = ctx.new_block("splitmsdone")
                    chk_b = ctx.new_block("splitmschk")
                    # A negative maxsplit means "no limit" (CPython).
                    ctx.emit(IRInstr("br.t", None, [neg_v, done_b.label, chk_b.label]))
                    ctx.switch_to(chk_b)
                    ctx.emit(IRInstr("br.t", None, [over_v, trim_b.label, done_b.label]))
                    ctx.switch_to(trim_b)
                    head_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", head_v, ["_abi_list_slice", v, zc_v, ms_v]))
                    tail_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", tail_v, ["_abi_list_slice", v, ms_v, plen_v]))
                    rest_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", rest_v, ["_abi_str_join", arg_v, tail_v]))
                    ctx.emit(IRInstr("call", None, ["_abi_list_append", head_v, rest_v]))
                    ctx.emit(IRInstr("store", None, [head_v, res_ptr]))
                    ctx.emit(IRInstr("br", None, [done_b.label]))
                    ctx.switch_to(done_b)
                    out_v = ctx.tmp(PTR)
                    ctx.emit(IRInstr("load", out_v, [res_ptr]))
                    return out_v
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
            if e.method == "translate" and len(e.args) == 1:
                # `s.translate(table)`: rebuild the string, substituting each
                # character present in the maketrans mapping (whose value may
                # be "" to delete it) and passing the rest through.
                tbl_v = _lower_expr(ctx, e.args[0])
                tr_len = ctx.tmp(I64)
                ctx.emit(IRInstr("call", tr_len, ["strlen", obj_v]))
                tr_acc = ctx.ensure_slot(f"__tr_acc_{id(e)}", PTR)
                tr_idx = ctx.ensure_slot(f"__tr_i_{id(e)}", I64)
                tr_empty = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", tr_empty, [ctx.mctx.intern_str("")]))
                ctx.emit(IRInstr("store", None, [tr_empty, tr_acc]))
                tz_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", tz_v, [0]))
                ctx.emit(IRInstr("store", None, [tz_v, tr_idx]))
                th_b = ctx.new_block("trhead")
                tb_b = ctx.new_block("trbody")
                tsub_b = ctx.new_block("trsub")
                tkeep_b = ctx.new_block("trkeep")
                tc_b = ctx.new_block("trcont")
                te_b = ctx.new_block("trend")
                ctx.emit(IRInstr("br", None, [th_b.label]))
                ctx.switch_to(th_b)
                ti_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", ti_v, [tr_idx]))
                tgo_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", tgo_v, [ti_v, tr_len]))
                ctx.emit(IRInstr("br.t", None, [tgo_v, tb_b.label, te_b.label]))
                ctx.switch_to(tb_b)
                tbi_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", tbi_v, [tr_idx]))
                tch_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", tch_v, ["_abi_str_char_at", obj_v, tbi_v]))
                thas_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", thas_v, ["_abi_dict_contains", tbl_v, tch_v]))
                ctx.emit(IRInstr("br.t", None, [thas_v, tsub_b.label, tkeep_b.label]))
                ctx.switch_to(tsub_b)
                tzz_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", tzz_v, [0]))
                trep_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", trep_v, ["_abi_dict_get_default", tbl_v, tch_v, tzz_v]))
                tacc1_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", tacc1_v, [tr_acc]))
                tnew1_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", tnew1_v, ["_abi_str_concat", tacc1_v, trep_v]))
                ctx.emit(IRInstr("store", None, [tnew1_v, tr_acc]))
                ctx.emit(IRInstr("br", None, [tc_b.label]))
                ctx.switch_to(tkeep_b)
                tacc2_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", tacc2_v, [tr_acc]))
                tnew2_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", tnew2_v, ["_abi_str_concat", tacc2_v, tch_v]))
                ctx.emit(IRInstr("store", None, [tnew2_v, tr_acc]))
                ctx.emit(IRInstr("br", None, [tc_b.label]))
                ctx.switch_to(tc_b)
                tci_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", tci_v, [tr_idx]))
                t1_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", t1_v, [1]))
                tnn_v = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", tnn_v, [tci_v, t1_v]))
                ctx.emit(IRInstr("store", None, [tnn_v, tr_idx]))
                ctx.emit(IRInstr("br", None, [th_b.label]))
                ctx.switch_to(te_b)
                tout_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", tout_v, [tr_acc]))
                return tout_v
            if e.method == "replace" and len(e.args) == 3:
                # `s.replace(old, new, count)` -- the runtime helper always
                # replaces every occurrence, so do a bounded walk instead:
                # repeatedly find the next `old`, append the text before it
                # plus `new`, and continue from just past the match; after
                # `count` replacements append whatever is left. count <= 0
                # replaces nothing (CPython).
                if A.expr_type(e.args[0]) != "str" or A.expr_type(e.args[1]) != "str":
                    raise LowerError("unsupported expr MethodCall (str.replace non-str arg)")
                old_v = _lower_expr(ctx, e.args[0])
                new_v = _lower_expr(ctx, e.args[1])
                cnt_v = _lower_expr(ctx, e.args[2])
                oldlen_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", oldlen_v, ["strlen", old_v]))
                acc_ptr = ctx.ensure_slot(f"__rep_acc_{id(e)}", PTR)
                rest_ptr = ctx.ensure_slot(f"__rep_rest_{id(e)}", PTR)
                done_ptr = ctx.ensure_slot(f"__rep_n_{id(e)}", I64)
                empty_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", empty_v, [ctx.mctx.intern_str("")]))
                ctx.emit(IRInstr("store", None, [empty_v, acc_ptr]))
                ctx.emit(IRInstr("store", None, [obj_v, rest_ptr]))
                rz_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", rz_v, [0]))
                ctx.emit(IRInstr("store", None, [rz_v, done_ptr]))
                rh_b = ctx.new_block("rephead")
                rb_b = ctx.new_block("repbody")
                rd_b = ctx.new_block("repdo")
                re_b = ctx.new_block("repend")
                ctx.emit(IRInstr("br", None, [rh_b.label]))
                ctx.switch_to(rh_b)
                rn_v = ctx.tmp(I64)
                ctx.emit(IRInstr("load", rn_v, [done_ptr]))
                rgo_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.lt", rgo_v, [rn_v, cnt_v]))
                ctx.emit(IRInstr("br.t", None, [rgo_v, rb_b.label, re_b.label]))
                ctx.switch_to(rb_b)
                rcur_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", rcur_v, [rest_ptr]))
                ridx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", ridx_v, ["_abi_str_index_of", rcur_v, old_v]))
                rz2_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", rz2_v, [0]))
                rfound_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.ge", rfound_v, [ridx_v, rz2_v]))
                ctx.emit(IRInstr("br.t", None, [rfound_v, rd_b.label, re_b.label]))
                ctx.switch_to(rd_b)
                rhead_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", rhead_v, ["_abi_str_slice", rcur_v, rz2_v, ridx_v]))
                racc_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", racc_v, [acc_ptr]))
                racc2_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", racc2_v, ["_abi_str_concat", racc_v, rhead_v]))
                racc3_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", racc3_v, ["_abi_str_concat", racc2_v, new_v]))
                ctx.emit(IRInstr("store", None, [racc3_v, acc_ptr]))
                rafter_v = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", rafter_v, [ridx_v, oldlen_v]))
                rcurlen_v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", rcurlen_v, ["strlen", rcur_v]))
                rtail_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", rtail_v, ["_abi_str_slice", rcur_v, rafter_v, rcurlen_v]))
                ctx.emit(IRInstr("store", None, [rtail_v, rest_ptr]))
                r1_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", r1_v, [1]))
                rnn_v = ctx.tmp(I64)
                ctx.emit(IRInstr("iadd", rnn_v, [rn_v, r1_v]))
                ctx.emit(IRInstr("store", None, [rnn_v, done_ptr]))
                ctx.emit(IRInstr("br", None, [rh_b.label]))
                ctx.switch_to(re_b)
                rfa_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", rfa_v, [acc_ptr]))
                rfr_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", rfr_v, [rest_ptr]))
                rout_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", rout_v, ["_abi_str_concat", rfa_v, rfr_v]))
                return rout_v
            raise LowerError(f"unsupported expr MethodCall (str.{e.method})")
        if obj_ty.startswith("mlang:"):
            # code.add(1, 2) where `code`'s static type is a `mlang:<uid>`
            # marker (see sema.py's _inject_mlang_if_needed / the
            # MethodCall `mlang:` dispatch it feeds). The receiver
            # (`code` itself) is purely a compile-time marker -- there is
            # no real runtime Code object, so `e.obj` is intentionally
            # NOT lowered/loaded here (unlike every other MethodCall
            # case): the call goes straight to the resolved C ABI symbol
            # (== the exported function's own name, guaranteed unmangled
            # by mlang_support's extern "C" auto-wrap), exactly like an
            # ordinary ffi_funcs call.
            uid = obj_ty.split(":", 1)[1]
            sig = ctx.mctx.mlang_code_funcs.get(uid, {}).get(e.method)
            if sig is None:
                raise LowerError(f"unsupported expr MethodCall (mlang Code.{e.method})")
            args = []
            for a, arg_ty in zip(e.args, sig.arg_types):
                av = _lower_expr(ctx, a)
                if arg_ty == "float" and av.type is not F64:
                    fv = ctx.tmp(F64)
                    ctx.emit(IRInstr("sitofp", fv, [av]))
                    av = fv
                args.append(av)
            res_ty = F64 if sig.ret_type == "float" else I64
            v = ctx.tmp(res_ty)
            ctx.emit(IRInstr("call", v, [e.method, *args]))
            return v
        if obj_ty.startswith("super:"):
            # super().method(args): dispatch statically to the base
            # class's method (never virtual -- codegen.py's own
            # `super:` handling does the same), with the *current*
            # method's `self` as the receiver. sema stamps `e.obj`'s
            # inferred_type as `super:<Base>` (see sema.py's `super()`
            # check) and already validated this only appears inside a
            # method, so `self` always has a bound param slot here. Was
            # entirely unimplemented on this backend: `super` as a bare
            # symbol fell through to a direct-symbol-call linking
            # against a nonexistent DLL import.
            parent = obj_ty.split(":", 1)[1]
            owner = _resolve_method_owner(ctx, parent, e.method)
            if owner is None:
                # Base isn't a user class this backend can dispatch to
                # (e.g. `class Foo(Exception)` calling
                # `super().__init__(msg)`) -- no extra base state to
                # initialize, matching codegen.py's own no-op here.
                # Still evaluate args for side effects.
                for a in e.args:
                    _lower_expr(ctx, a)
                return ctx.shared_zero
            self_ptr = _name_ptr(ctx, "self", PTR)
            self_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", self_v, [self_ptr]))
            args = [self_v] + [_lower_expr(ctx, a) for a in e.args]
            res_ty = ir_type_for(A.expr_type(e))
            v = ctx.tmp(res_ty)
            ctx.emit(IRInstr("call", v, [f"{owner}__{e.method}", *args]))
            return v
        if obj_ty == "instance:DynamicModule":
            # `handle.func(args)` where handle = import_binary(path) and
            # func is a `@handle.imported` stub -- see the "import_binary
            # dynamic DLL loading" section near the end of this file for
            # the matching import_binary(path) assignment lowering. Must
            # be checked BEFORE the generic "instance:" case just below:
            # a DynamicModule handle is represented as an ordinary
            # instance dict (same _abi_new_instance/_abi_dict_* machinery
            # every user class uses), but `e.method` is never a real
            # ClassName__method symbol -- it's a function pointer resolved
            # at runtime and stashed in the handle's own dict, keyed by
            # name.
            return _lower_dynamic_call(ctx, e)
        if obj_ty.startswith("instance:"):
            # Walk the inheritance chain for the method's actual defining
            # class -- a static instance:Dog type doesn't mean Dog itself
            # defines every method; an inherited-but-not-overridden one
            # (e.g. a base class's @property with no subclass override)
            # is only ever emitted as Animal__greeting, never Dog__greeting.
            cls_name = obj_ty.split(":", 1)[1]
            owner = _resolve_method_owner(ctx, cls_name, e.method) or cls_name
            obj_v = _lower_expr(ctx, e.obj)
            # Each argument is a store into the method parameter's slot
            # (parameter 0 is `self`, so call argument i maps to i+1).
            _mann = _callee_param_annots(ctx, f"{owner}__{e.method}")
            args = [obj_v] + [
                _lower_call_arg(ctx, a, _mann[i + 1] if i + 1 < len(_mann) else None)
                for i, a in enumerate(e.args)
            ]
            res_ty = ir_type_for(A.expr_type(e))

            rows = _virtual_dispatch_rows(ctx, cls_name, e.method)
            owners: list[str] = []
            for _cid, ow in rows:
                if ow not in owners:
                    owners.append(ow)
            if len(owners) <= 1:
                # No subclass overrides this method -- bind statically.
                # `overload` extension: sema stamps the resolved (mangled,
                # bare-name) symbol for a dispatched overload method call --
                # combine it with `owner` the same way an ordinary method's
                # bare name is combined, since codegen's own class-name-
                # prefixing step already ran on the RENAMED (mangled)
                # FuncDef during sema's overload pre-pass.
                resolved_ov_m = getattr(e, "resolved_overload_symbol", None)
                method_part = resolved_ov_m if resolved_ov_m is not None else e.method
                v = ctx.tmp(res_ty)
                ctx.emit(IRInstr("call", v, [f"{owner}__{method_part}", *args]))
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
            # Pass this class's RTTI id as the implicit `cls` (not shared_zero):
            # the classmethod body may read a `cls.<classvar>` that a subclass
            # overrides, dispatched at runtime on the receiver's class id
            # (dynamic_classvar_compat_fixes). Even a LITERAL `Server.method()`
            # therefore needs the real id so that read resolves to Server's
            # override rather than falling through to the id-0/null default. A
            # staticmethod takes no receiver.
            if is_static:
                args = call_args
            else:
                cls_id_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", cls_id_v, [ctx.mctx.class_ids[e.obj.name]]))
                args = [cls_id_v] + call_args
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [sym, *args]))
            return v
        # `@classmethod`/`@staticmethod` call on a `type` value whose concrete
        # class isn't a literal name -- e.g. iterating a tuple of classes
        # (`for cls in (Server, Shared, Client): cls.supports_runtime(...)`).
        # A `type` value already *is* the class's RTTI id (that's how a bare
        # class name lowers, see the A.Name branch), so unlike the opaque-
        # instance dispatch below we use the receiver directly as the class id
        # rather than reading a `__class__` tag off an instance dict. Class
        # methods receive an implicit `cls` (shared_zero -- asmpython has no
        # class objects and classmethod bodies that only touch class vars /
        # call other statics never dereference it); static methods take the
        # args verbatim. An equality chain over every candidate class id routes
        # to the concrete `{owner}__{method}`.
        if obj_ty == "type":
            type_rows = _classes_resolving_method(ctx, e.method)
            if type_rows:
                recv_v = _lower_expr(ctx, e.obj)  # the class id itself
                first_owner = type_rows[0][1]
                is_static = _resolved_method_is_static(ctx, first_owner, e.method)
                _mann = _callee_param_annots(ctx, f"{first_owner}__{e.method}")
                # self/cls is param 0 for classmethods, so a call arg i maps to
                # param i+1; a staticmethod has no implicit param, so arg i maps
                # to param i.
                ann_shift = 0 if is_static else 1
                call_args = [
                    _lower_call_arg(
                        ctx,
                        a,
                        _mann[i + ann_shift] if i + ann_shift < len(_mann) else None,
                    )
                    for i, a in enumerate(e.args)
                ]
                res_ty = ir_type_for(A.expr_type(e))
                res_ptr = ctx.ensure_slot(f"__typedisp_res_{id(e)}", res_ty)

                check_blocks = [ctx.new_block(f"typedispcheck{i}") for i in range(len(type_rows))]
                hit_blocks = [ctx.new_block(f"typedisphit{i}") for i in range(len(type_rows))]
                stub_b = ctx.new_block("typedispstub")
                end_b = ctx.new_block("typedispend")

                ctx.emit(IRInstr("br", None, [check_blocks[0].label]))

                for i, (cid, ow) in enumerate(type_rows):
                    ctx.switch_to(check_blocks[i])
                    cid_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", cid_v, [cid]))
                    is_match = ctx.tmp(I64)
                    ctx.emit(IRInstr("icmp.eq", is_match, [recv_v, cid_v]))
                    next_label = check_blocks[i + 1].label if i + 1 < len(check_blocks) else stub_b.label
                    ctx.emit(IRInstr("br.t", None, [is_match, hit_blocks[i].label, next_label]))

                    ctx.switch_to(hit_blocks[i])
                    # Pass the MATCHED class id as the implicit `cls` (not
                    # shared_zero) -- a classmethod body may dispatch a
                    # `cls.<classvar>` read on it (dynamic_classvar_compat_fixes
                    # keys off the receiver's runtime class id), so `cls` must
                    # carry the concrete class, not null. This block only runs
                    # when `recv_v == cid`, so the constant `cid` IS that value.
                    # A staticmethod takes no receiver.
                    hit_cid_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", hit_cid_v, [cid]))
                    args = call_args if is_static else [hit_cid_v] + call_args
                    mv = ctx.tmp(res_ty)
                    ctx.emit(IRInstr("call", mv, [f"{ow}__{e.method}", *args]))
                    ctx.emit(IRInstr("store", None, [mv, res_ptr]))
                    ctx.emit(IRInstr("br", None, [end_b.label]))

                # Class id matched no candidate: a genuine opaque type value.
                # Preserve the graceful-stub behaviour and yield 0.
                ctx.switch_to(stub_b)
                stub_v = ctx.tmp(res_ty)
                ctx.emit(IRInstr("const", stub_v, [0]))
                ctx.emit(IRInstr("store", None, [stub_v, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

                ctx.switch_to(end_b)
                out = ctx.tmp(res_ty)
                ctx.emit(IRInstr("load", out, [res_ptr]))
                return out
        # Method call on an opaque/`any`-typed receiver whose static type
        # names no class (e.g. a value read out of a `list[object]`, an
        # `object` parameter, or an `any` return). If some user class defines
        # a method of this name, the receiver may AT RUNTIME be an instance of
        # one of those classes -- do real virtual dispatch on its runtime
        # `__class__` id instead of the old graceful no-op that returned 0.
        #
        # This is the general form of the `instance:`/`super:` dispatch above:
        # there the base class is statically known, here it is not, so the
        # candidate set spans every class resolving the method
        # (`_classes_resolving_method`). The receiver's runtime tag is read the
        # same way `type()`/`isinstance()` read it (`_lower_read_any_tag`),
        # which returns the class id an instance dict carries under
        # "__class__". An equality chain over the candidate class ids selects
        # the concrete `{owner}__{method}` to call; anything that matches no
        # candidate (a genuine opaque FFI value) falls through to the graceful
        # stub, preserving the old survive-unmodeled-methods behaviour.
        disp_rows = _classes_resolving_method(ctx, e.method)
        if disp_rows:
            recv_v = _lower_expr(ctx, e.obj)
            # Box/scalar-forward each argument the same way a statically-bound
            # method call does. All candidate owners share the method name; use
            # the first owner's recorded parameter annotations to decide arg
            # boxing (self is param 0, so call arg i maps to param i+1).
            first_owner = disp_rows[0][1]
            _mann = _callee_param_annots(ctx, f"{first_owner}__{e.method}")
            args = [recv_v] + [
                _lower_call_arg(ctx, a, _mann[i + 1] if i + 1 < len(_mann) else None)
                for i, a in enumerate(e.args)
            ]
            res_ty = ir_type_for(A.expr_type(e))

            class_id = _lower_read_any_tag(ctx, recv_v)
            res_ptr = ctx.ensure_slot(f"__anydisp_res_{id(e)}", res_ty)

            # Distinct owners, in row order, so each concrete class id routes to
            # its own resolved method. Multiple class ids can share one owner
            # (an inherited, non-overridden method); the per-id equality checks
            # below still route each correctly.
            check_blocks = [ctx.new_block(f"anydispcheck{i}") for i in range(len(disp_rows))]
            hit_blocks = [ctx.new_block(f"anydisphit{i}") for i in range(len(disp_rows))]
            stub_b = ctx.new_block("anydispstub")
            end_b = ctx.new_block("anydispend")

            ctx.emit(IRInstr("br", None, [check_blocks[0].label]))

            for i, (cid, ow) in enumerate(disp_rows):
                ctx.switch_to(check_blocks[i])
                cid_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", cid_v, [cid]))
                is_match = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", is_match, [class_id, cid_v]))
                next_label = check_blocks[i + 1].label if i + 1 < len(check_blocks) else stub_b.label
                ctx.emit(IRInstr("br.t", None, [is_match, hit_blocks[i].label, next_label]))

                ctx.switch_to(hit_blocks[i])
                mv = ctx.tmp(res_ty)
                ctx.emit(IRInstr("call", mv, [f"{ow}__{e.method}", *args]))
                ctx.emit(IRInstr("store", None, [mv, res_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))

            # Receiver matched no candidate class id: a genuine opaque value
            # (unmodeled FFI object, or a runtime type this method name doesn't
            # belong to). Preserve the old graceful behaviour -- yield 0.
            ctx.switch_to(stub_b)
            stub_v = ctx.tmp(res_ty)
            ctx.emit(IRInstr("const", stub_v, [0]))
            ctx.emit(IRInstr("store", None, [stub_v, res_ptr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(end_b)
            out = ctx.tmp(res_ty)
            ctx.emit(IRInstr("load", out, [res_ptr]))
            return out

        # No user class defines this method: a genuinely opaque/FFI method.
        # Evaluate receiver and args for side effects and return 0.  Mirrors
        # codegen.py's graceful stub so selfhost builds survive unmodeled FFI
        # methods.
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
        if (
            isinstance(e.obj, A.Name)
            and ctx.receiver_param is not None
            and e.obj.name == ctx.receiver_param
            and ctx.method_owner_class is not None
        ):
            # `self.<classvar>` / `cls.<classvar>` inside a method body, where
            # <classvar> is a class-level variable declared on the owning class
            # or an ancestor (e.g. `class RealmRoot: runtime_realms = (...)`;
            # `cls.runtime_realms` in its @classmethod). Bind statically to the
            # resolved owner's `__cv_<Owner>__<var>` global. Essential for a
            # @classmethod: its `cls` is a null placeholder (asmpython has no
            # runtime class objects), so a class-var read through it must NOT
            # dereference the receiver -- and because each subclass compiles its
            # own `Subclass__method` symbol, resolving up *this* owner's chain
            # picks up the right per-subclass override. An INSTANCE attribute of
            # the same name (set in __init__) is handled by the generic fallback
            # below and is unaffected: this only fires for names that resolve to
            # a real class-var label. Missing here previously meant `cls.tag`
            # read through the null `cls` and returned 0.
            for owner in _resolve_class_chain(ctx, ctx.method_owner_class):
                if (owner, e.name) in ctx.mctx.class_var_labels:
                    label = ctx.mctx.class_var_labels[(owner, e.name)]
                    ty = ctx.mctx.global_types.get(label, ir_type_for(A.expr_type(e)))
                    ptr = ctx.tmp(PTR)
                    ctx.emit(IRInstr("global_addr", ptr, [label]))
                    v = ctx.tmp(ty)
                    ctx.emit(IRInstr("load", v, [ptr]))
                    return v
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
            # A binding that means something different when READ than when
            # called (`read_c_name`) resolves by calling that symbol. Without
            # this a bare `mod.Thing` falls through to the instance-attribute
            # fallback and reads the module name as a pointer, which is not a
            # miscompile so much as a segfault waiting for a caller.
            read_symbol = getattr(b, "read_c_name", None) if b is not None else None
            if read_symbol:
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, [read_symbol]))
                return v
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
        # An "any"-typed field holds a box-or-raw pointer-sized value; type it
        # PTR so the read choke point (`_lower_expr`) auto-unboxes it (its gate
        # is `v.type is PTR`). A concretely-typed field stays I64 as before.
        v = ctx.tmp(PTR if A.expr_type(e) == "any" else I64)
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
        # Copy inherited class-body defaults and dynamic class namespace
        # assignments base-to-derived, so normal `self.NAME` reads see the
        # effective class attribute and subclass overrides win.
        class_chain = list(reversed(_resolve_class_chain(ctx, e.func)))
        for owner in class_chain:
            for (declaring_class, attribute), label in ctx.mctx.class_var_labels.items():
                if declaring_class != owner:
                    continue
                value_ptr = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", value_ptr, [label]))
                value_ty = ctx.mctx.global_types.get(label, I64)
                value = ctx.tmp(value_ty)
                ctx.emit(IRInstr("load", value, [value_ptr]))
                key_name = ctx.mctx.intern_str(attribute)
                key = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", key, [key_name]))
                stored = value
                if value.type is F64:
                    stored = ctx.tmp(I64)
                    ctx.emit(IRInstr("bitcast_f2i", stored, [value]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_set", v, key, stored]))
            class_label = ctx.mctx.class_object_labels.get(owner)
            if class_label is not None:
                class_ptr = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", class_ptr, [class_label]))
                class_object = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", class_object, [class_ptr]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_update", v, class_object]))
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
            # Each argument is a store into the constructor parameter's slot
            # (parameter 0 is `self`, so call argument i maps to i+1).
            _iann = _callee_param_annots(ctx, f"{owner}____init__")
            for i, arg in enumerate(e.args):
                init_args.append(
                    _lower_call_arg(ctx, arg, _iann[i + 1] if i + 1 < len(_iann) else None)
                )
            ctx.emit(IRInstr("call", None, [f"{owner}____init__", *init_args]))
        return v

    if isinstance(e, A.Call) and e.func in ctx.mctx.ffi_funcs:
        # A bound stdlib FFI function (e.g. asmlib.hardware.in_byte/cpuid/
        # disable_interrupts, or a real libm export like math.sqrt): call
        # its real c_name symbol, not the asmpython-level name. Argument
        # marshaling is exactly what a normal "call" IR op already does
        # (the same standard-ABI argument passing _gen_ffi_call does by
        # hand in the legacy codegen.py for the same bindings) -- but the
        # RESULT type must follow the binding's own declared `ret_type`,
        # not be hardcoded I64: hardware.py's bindings are genuinely all
        # int, but math.py's aren't (sqrt/sin/cos/... return float, in
        # XMM0, not RAX). Was hardcoded I64 unconditionally -- confirmed
        # via a real repro (`int(sqrt(49))`): the call's result got typed
        # I64 despite the real ABI return coming back in XMM0, so the
        # immediately-following `fptosi` (which expects an F64 SOURCE
        # location) fed it a value the allocator had placed in a GP
        # register, tripping codegen's `_dst_xmm`-style location-kind
        # assert (`'RegLoc' object has no attribute 'offset'`).
        fn = ctx.mctx.ffi_funcs[e.func]
        c_name = getattr(fn, "c_name_windows", None) or fn.c_name
        args = []
        for i, a in enumerate(e.args):
            av = _lower_expr(ctx, a)
            # Coerce an int-typed argument to float when the binding
            # declares a float parameter (e.g. `sqrt(49)` -- a bare int
            # literal into a `("float",)`-typed binding): without this,
            # the raw integer bits get passed through unconverted and
            # reinterpreted as a double bit pattern on the callee side,
            # producing garbage (confirmed: `sqrt(49)` silently returned
            # `0` instead of `7`). No reverse case needed -- a real
            # Python float literal/expression passed to an int-typed
            # parameter isn't valid input this compiler needs to handle
            # leniently, unlike this int-into-float direction, which is
            # extremely common (any bare int literal argument).
            if (
                i < len(fn.arg_types)
                and fn.arg_types[i] == "float"
                and av.type is not F64
            ):
                fv = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", fv, [av]))
                av = fv
            args.append(av)
        res_ty = F64 if fn.ret_type == "float" else I64
        v = ctx.tmp(res_ty)
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

            if arg_t == "any":
                # `int(x)` on an opaque value that may be a boxed scalar cell
                # (a scalar read out of a `dict[str, object]` / `list[object]`,
                # see `_lower_value_into_any_slot`): unbox to the raw payload.
                # A never-boxed opaque value passes through unchanged (unbox is
                # a shape-safe no-op on a non-cell), so this is always safe.
                obj_v = _lower_expr_inner(ctx, arg)
                return _lower_unbox_any(ctx, obj_v)
            return _lower_expr(ctx, arg)
        if e.func == "float" and len(e.args) == 1:
            arg = e.args[0]
            arg_t = A.expr_type(arg)
            if arg_t == "any":
                # Mirror of `int(x)` on an opaque value above: a scalar read
                # out of a list[object] -- or an unannotated *args -- is a
                # boxed cell. The payload holds the double's BITS, so
                # reinterpret rather than convert. Without this, every float
                # passed through *args read back as 0.0.
                obj_v = _lower_expr_inner(ctx, arg)
                payload = _lower_unbox_any(ctx, obj_v)
                out = ctx.tmp(F64)
                ctx.emit(IRInstr("bitcast_i2f", out, [payload]))
                return out
            # float("nan")/float("inf")/float("-inf") emit the bit
            # pattern directly rather than relying on strtod (matches
            # codegen.py's own special-case, since UCRT's strtod
            # historically had "nan"/"inf" parsing quirks).
            if isinstance(arg, A.StrLit):
                s = arg.value.strip().lower()
                if s == "nan":
                    out = ctx.tmp(F64)
                    ctx.emit(IRInstr("const", out, [float("nan")]))
                    return out
                if s in ("inf", "+inf", "infinity", "+infinity"):
                    out = ctx.tmp(F64)
                    ctx.emit(IRInstr("const", out, [float("inf")]))
                    return out
                if s in ("-inf", "-infinity"):
                    out = ctx.tmp(F64)
                    ctx.emit(IRInstr("const", out, [float("-inf")]))
                    return out
            if arg_t == "str":
                # float(str) was entirely unimplemented on this backend
                # -- `e.func` (the bare name "float") fell through to
                # the generic direct-symbol-call fallback, linking
                # against a nonexistent symbol `float`. strtod(ptr,
                # NULL) parses directly into XMM0 -- no ABI shim needed,
                # a plain IR call with a float result already marshals
                # correctly.
                str_v = _lower_expr(ctx, arg)
                null_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("const", null_v, [0]))
                out = ctx.tmp(F64)
                ctx.emit(IRInstr("call", out, ["strtod", str_v, null_v]))
                return out
            if arg_t == "int":
                int_v = _lower_expr(ctx, arg)
                out = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", out, [int_v]))
                return out
            if arg_t.startswith("instance:"):
                # `float(obj)` converts through the class's `__float__`, the
                # mirror of `int()`'s `__int__` dispatch. Deliberately NO
                # cross-fallback between the two dunders: CPython raises
                # TypeError for `int(x)` on a `__float__`-only class and for
                # `float(x)` on an `__int__`-only one, so accepting either
                # would take a program CPython rejects.
                _fowner = _resolve_method_owner(
                    ctx, arg_t.split(":", 1)[1], "__float__"
                )
                if _fowner is not None:
                    obj_v = _lower_expr(ctx, arg)
                    out = ctx.tmp(F64)
                    ctx.emit(
                        IRInstr("call", out, [f"{_fowner}____float__", obj_v])
                    )
                    return out

            # float -> float: identity.
            return _lower_expr(ctx, arg)
        if e.func in ("min", "max") and len(e.args) >= 1:
            return _lower_minmax(ctx, e)
        if e.func == "sorted" and len(e.args) == 1:
            return _lower_sorted(ctx, e)
        if e.func in _DESCRIPTOR_WRAPPERS and len(e.args) <= 1:
            return _lower_descriptor_wrapper(ctx, e)
        if e.func == "slice" and 1 <= len(e.args) <= 3:
            return _lower_slice_ctor(ctx, e)
        if e.func == "type" and len(e.args) == 1:
            arg = e.args[0]
            arg_t = A.expr_type(arg)
            if arg_t != "any":
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
            # `type(x)` where x's STATIC type is "any": the answer isn't
            # known at compile time. Read the runtime tag `_lower_box_any`
            # stamped on x (mirroring `isinstance`'s "any" branch just
            # above) and pick the matching pre-interned class-name string
            # at runtime. Falls back to "" (this call site's own
            # pre-existing unknown-type behavior) for a tag this dispatch
            # doesn't recognize -- an ordinary never-boxed container/
            # instance whose tag isn't one of the scalar BUILTIN_TYPE_IDS
            # entries below. Uses `_lower_expr_inner`, not `_lower_expr`,
            # so it sees the raw (possibly still-boxed) cell.
            obj_v = _lower_expr_inner(ctx, arg)
            tag_v = _lower_read_any_tag(ctx, obj_v)
            out_ptr = ctx.ensure_slot(f"__typeof_out_{id(e)}", PTR)
            empty_sym = ctx.mctx.intern_str("")
            empty_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", empty_v, [empty_sym]))
            ctx.emit(IRInstr("store", None, [empty_v, out_ptr]))
            end_b = ctx.new_block("typeofend")
            for kind, tag in (
                ("bool", BUILTIN_TYPE_IDS["bool"]),
                ("NoneType", NONE_TYPE_ID),
                ("int", BUILTIN_TYPE_IDS["int"]),
                ("float", BUILTIN_TYPE_IDS["float"]),
                ("str", BUILTIN_TYPE_IDS["str"]),
            ):
                match_b = ctx.new_block(f"typeof_{kind}")
                next_b = ctx.new_block(f"typeofnext_{kind}")
                tag_const = ctx.tmp(I64)
                ctx.emit(IRInstr("const", tag_const, [tag]))
                eq_v = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", eq_v, [tag_v, tag_const]))
                ctx.emit(IRInstr("br.t", None, [eq_v, match_b.label, next_b.label]))
                ctx.switch_to(match_b)
                text_sym = ctx.mctx.intern_str(f"<class '{kind}'>")
                text_v = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", text_v, [text_sym]))
                ctx.emit(IRInstr("store", None, [text_v, out_ptr]))
                ctx.emit(IRInstr("br", None, [end_b.label]))
                ctx.switch_to(next_b)
            ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(end_b)
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", out, [out_ptr]))
            return out
        if e.func == "chr" and len(e.args) == 1:
            n_v = _lower_expr(ctx, e.args[0])
            out = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", out, ["_abi_chr", n_v]))
            return out
        if e.func == "repr" and len(e.args) == 1:
            return _lower_expr_as_str(ctx, e.args[0], repr_mode=True)
        if e.func == "format" and len(e.args) in (1, 2):
            # format(value[, spec]) -- entirely unimplemented before this;
            # fell through to the generic bare-symbol-call fallback,
            # linking against a nonexistent `format` DLL symbol. Reuses
            # `_lower_fstring_segment`'s spec-parsing/formatting machinery
            # exactly like `_lower_str_format` does for `"...".format()`
            # -- stamp `fmt_spec` (a compile-time literal, same
            # requirement f-strings/`.format()` already have -- a
            # runtime-computed spec string isn't supported by any of the
            # three) onto the value expression and reuse the shared
            # per-value formatter.
            val_arg = e.args[0]
            if len(e.args) == 2:
                spec_arg = e.args[1]
                if not isinstance(spec_arg, A.StrLit):
                    raise LowerError("unsupported expr Call (format() with a non-literal spec)")
                val_arg.fmt_spec = spec_arg.value  # type: ignore[attr-defined]
            else:
                val_arg.fmt_spec = ""  # type: ignore[attr-defined]
            val_arg.conv_flag = ""  # type: ignore[attr-defined]
            return _lower_fstring_segment(ctx, val_arg)
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
        if e.func == "abs" and len(e.args) == 1:
            # Was entirely unimplemented: fell through to the generic
            # bare-symbol-call fallback, linking against libc's real
            # `abs()`/`labs()` (declared in pe_linker.py's _DLL_FOR_SYMBOL
            # for other, legitimate uses) and calling it on WHATEVER value
            # the argument lowered to -- for an instance (e.g. Fraction),
            # that's the raw heap pointer, which libc's abs() happily
            # "absolute-values" as if it were a plain int (a no-op on any
            # positive pointer) instead of dispatching to `__abs__`, so
            # `abs(Fraction(-3, 4))` silently returned the ORIGINAL,
            # still-negative Fraction unchanged.
            arg_t = A.expr_type(e.args[0])
            if arg_t.startswith("instance:"):
                cls_name = arg_t.split(":", 1)[1]
                owner = _resolve_method_owner(ctx, cls_name, "__abs__")
                if owner is not None:
                    val = _lower_expr(ctx, e.args[0])
                    v = ctx.tmp(ir_type_for(A.expr_type(e)))
                    ctx.emit(IRInstr("call", v, [f"{owner}____abs__", val]))
                    return v
            if arg_t == "float":
                f_v = _lower_expr(ctx, e.args[0])
                out = ctx.tmp(F64)
                ctx.emit(IRInstr("call", out, ["fabs", f_v]))
                return out
            n_v = _lower_expr(ctx, e.args[0])
            out = ctx.tmp(I64)
            ctx.emit(IRInstr("call", out, ["labs", n_v]))
            return out
        if e.func == "hash" and len(e.args) == 1:
            # `hash(instance)` on a class defining __hash__, `hash(str)`
            # (FNV-1a via the dict runtime's own string hasher), and
            # `hash(int/float/bool)` (identity -- the raw value itself,
            # matching codegen.py's own "value already in rax" comment).
            # Was entirely unimplemented for every case, falling through
            # to the generic bare-symbol-call fallback, linking against a
            # nonexistent `hash` DLL symbol (Python's hash() has no libc
            # equivalent at all, unlike abs()).
            arg_t = A.expr_type(e.args[0])
            if arg_t.startswith("instance:"):
                cls_name = arg_t.split(":", 1)[1]
                owner = _resolve_method_owner(ctx, cls_name, "__hash__")
                if owner is not None:
                    val = _lower_expr(ctx, e.args[0])
                    v = ctx.tmp(I64)
                    ctx.emit(IRInstr("call", v, [f"{owner}____hash__", val]))
                    return v
                raise LowerError("unsupported expr Call (hash() on a non-instance or a class with no __hash__)")
            if arg_t == "str":
                val = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, ["_abi_hash_string", val]))
                return v
            if arg_t in ("int", "bool", "any"):
                return _lower_expr(ctx, e.args[0])
            if arg_t == "float":
                # Identity hash on the raw bits, same as codegen.py's
                # "value already in rax" comment -- CPython's real
                # float-hash algorithm (normalizing e.g. hash(2.0) ==
                # hash(2)) isn't replicated here, matching the existing
                # int/bool simplification's own precedent.
                val = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", v, [val]))
                return v
            raise LowerError("unsupported expr Call (hash() on a non-instance or a class with no __hash__)")
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
        if e.func == "round" and len(e.args) == 2:
            # round(x, ndigits) -> float (unlike the 1-arg form, ndigits
            # keeps the result a float even for int x -- Python 3's own
            # documented `round(x, n)` type-preservation rule reduces to
            # "always float" here since asmpython has no separate
            # Decimal/int-exact path). Scale by 10**ndigits, reuse the
            # existing _abi_round_f64 shim (SSE4.1 roundsd, ties-to-even,
            # same banker's-rounding the 1-arg form already uses), then
            # unscale -- mirrors codegen.py's own round(x, ndigits) case
            # exactly (mulsd/roundsd/divsd around a real `pow` call for
            # 10**n). Was entirely unimplemented on this backend: only
            # the 1-arg form existed, so `round(x, 6)` fell through to a
            # direct-symbol-call linking against a nonexistent `round`.
            x_t = A.expr_type(e.args[0])
            x_v = _lower_expr(ctx, e.args[0])
            if x_t != "float":
                xf = ctx.tmp(F64)
                ctx.emit(IRInstr("sitofp", xf, [x_v]))
                x_v = xf
            nd_v = _lower_expr(ctx, e.args[1])
            nd_f = ctx.tmp(F64)
            ctx.emit(IRInstr("sitofp", nd_f, [nd_v]))
            ten = ctx.tmp(F64)
            ctx.emit(IRInstr("const", ten, [10.0]))
            scale = ctx.tmp(F64)
            ctx.emit(IRInstr("call", scale, ["pow", ten, nd_f]))
            scaled = ctx.tmp(F64)
            ctx.emit(IRInstr("fmul", scaled, [x_v, scale]))
            rounded = ctx.tmp(F64)
            ctx.emit(IRInstr("call", rounded, ["_abi_round_f64", scaled]))
            out = ctx.tmp(F64)
            ctx.emit(IRInstr("fdiv", out, [rounded, scale]))
            return out
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
            if getattr(e, "divmod_float", False):
                # FLOAT divmod: `(floor(a / b), a - floor(a / b) * b)`, which is
                # Python's floor-division pair for floats. `_abi_divmod` is an
                # integer helper -- handing it doubles reinterpreted the bit
                # patterns as integers.
                _fa = _lower_expr(ctx, e.args[0])
                if _fa.type is not F64:
                    _t = ctx.tmp(F64)
                    ctx.emit(IRInstr("sitofp", _t, [_fa]))
                    _fa = _t
                _fb = _lower_expr(ctx, e.args[1])
                if _fb.type is not F64:
                    _t2 = ctx.tmp(F64)
                    ctx.emit(IRInstr("sitofp", _t2, [_fb]))
                    _fb = _t2
                _q_raw = ctx.tmp(F64)
                ctx.emit(IRInstr("fdiv", _q_raw, [_fa, _fb]))
                _q = ctx.tmp(F64)
                ctx.emit(IRInstr("call", _q, ["floor", _q_raw]))
                _qb = ctx.tmp(F64)
                ctx.emit(IRInstr("fmul", _qb, [_q, _fb]))
                _r = ctx.tmp(F64)
                ctx.emit(IRInstr("fsub", _r, [_fa, _qb]))
                # The pair is a 2-slot tuple; a float in a raw 8-byte cell has
                # to cross as its BITS (the recurring float-in-container rule).
                _cap = ctx.tmp(I64)
                ctx.emit(IRInstr("const", _cap, [2]))
                _tup = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", _tup, ["_abi_new_list", _cap]))
                for _slot in (_q, _r):
                    _bits = ctx.tmp(I64)
                    ctx.emit(IRInstr("bitcast_f2i", _bits, [_slot]))
                    ctx.emit(IRInstr("call", None, ["_abi_list_append", _tup, _bits]))
                return _tup
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
            # Store each argument into the __call__ parameter's slot
            # (parameter 0 is `self`, so call argument i maps to i+1).
            _cann = _callee_param_annots(ctx, f"{call_owner}____call__")
            args = [obj_v] + [
                _lower_call_arg(ctx, a, _cann[i + 1] if i + 1 < len(_cann) else None)
                for i, a in enumerate(e.args)
            ]
            v = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(IRInstr("call", v, [f"{call_owner}____call__", *args]))
            return v
        if getattr(e, "closure_call_on_expr", False):
            # A closure VALUE produced by an expression (`adder(5)(10)`).
            # Identical dispatch to the name-callee case below, but the object
            # comes from evaluating `func_expr` instead of loading a slot.
            return _emit_closure_value_call(
                ctx, _lower_expr_inner(ctx, e.func_expr), e
            )
        if e.func in ctx.closure_value_names:
            # Call an ESCAPING closure value (`add5 = make_adder(5); add5(10)`).
            # add5 holds a closure OBJECT [magic, fn_ptr, cap0..capN-1] whose
            # capture count N isn't known here (the object came from a factory).
            # sema typed add5 "closure", so it's safe to read as a list object.
            # Read fn_ptr and the runtime count (list length - 2), then branch
            # on N to emit a fixed-arity `fn(cap0..capN-1, args...)` call per
            # candidate N -- the leading-captured-params calling convention the
            # lifted function already expects (same shape as the closure_names
            # path, but with a runtime count instead of a static one).
            return _emit_closure_value_call(
                ctx, _lower_expr_inner(ctx, A.Name(name=e.func, pos=e.pos)), e
            )
        if e.func in ctx.closure_names:
            closure = _lower_expr(ctx, A.Name(name=e.func, pos=e.pos))
            buffer_address = ctx.tmp(PTR)
            ctx.emit(
                IRInstr("gep", buffer_address, [closure, _LIST_BUF_OFF])
            )
            buffer = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", buffer, [buffer_address]))
            function_address = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", function_address, [buffer, 8]))
            function = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", function, [function_address]))
            captured: list[IRValue] = []
            for index in range(ctx.closure_free_counts.get(e.func, 0)):
                address = ctx.tmp(PTR)
                ctx.emit(
                    IRInstr("gep", address, [buffer, (index + 2) * 8])
                )
                value = ctx.tmp(I64)
                ctx.emit(IRInstr("load", value, [address]))
                captured.append(value)
            args = [_lower_expr(ctx, argument) for argument in e.args]
            result = ctx.tmp(ir_type_for(A.expr_type(e)))
            ctx.emit(
                IRInstr("call", result, [function, *captured, *args])
            )
            return result
        captured_args: list[IRValue] = []
        lifted_free_vars = ctx.mctx.lifted_free_vars.get(e.func, [])
        lifted_nonlocals = ctx.mctx.lifted_nonlocal_vars.get(e.func, set())
        for free_name in lifted_free_vars:
            if free_name in lifted_nonlocals:
                if free_name in ctx.nonlocal_names:
                    # This function received the same shared box as one of
                    # its own hidden closure parameters.
                    slot = _name_ptr(ctx, free_name, PTR)
                    captured = ctx.tmp(PTR)
                    ctx.emit(IRInstr("load", captured, [slot]))
                elif free_name in ctx.boxed_names:
                    box_slot = ctx.ensure_slot(f"__nl_box_{free_name}", PTR)
                    captured = ctx.tmp(PTR)
                    ctx.emit(IRInstr("load", captured, [box_slot]))
                else:
                    # A direct call after binding should normally have boxed
                    # this already. Keep the path correct for a compiler-
                    # synthesized direct call by creating that box lazily.
                    current = _lower_expr(
                        ctx, A.Name(name=free_name, pos=e.pos)
                    )
                    size = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", size, [8]))
                    captured = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", captured, ["malloc", size]))
                    ctx.emit(IRInstr("store", None, [current, captured]))
                    box_slot = ctx.ensure_slot(f"__nl_box_{free_name}", PTR)
                    ctx.emit(IRInstr("store", None, [captured, box_slot]))
                    ctx.boxed_names.add(free_name)
            else:
                captured = _lower_expr(
                    ctx, A.Name(name=free_name, pos=e.pos)
                )
            captured_args.append(captured)
        _pann = _callee_param_annots(ctx, e.func)
        args = captured_args + [
            _lower_call_arg(ctx, a, _pann[i] if i < len(_pann) else None)
            for i, a in enumerate(e.args)
        ]
        if getattr(e, "dstar_dynamic", False) and e.dstar is not None:
            # `target(**kwargs)` against an opaque callable value: sema left
            # the runtime dict in `e.dstar` (never expanded it against a
            # non-existent static param list). Pass it as the call's trailing
            # argument -- the exact ABI slot a `**kwargs`-declared callee
            # receives its packed dict in (see sema's `_bind_args`, which
            # appends the DictLit last for a known `**kwargs` function). The
            # callee is responsible for accepting a `**kwargs` parameter.
            dstar_dict = _lower_expr(ctx, e.dstar)
            args = args + [dstar_dict]
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
            # The call TARGET is a function pointer, never a boxed scalar, so
            # read it raw (`_lower_expr_inner`) -- the auto-unboxing
            # `_lower_expr` would run an "any"-typed callable variable through
            # the box tag-read/unbox path and corrupt the function pointer.
            target = _lower_expr_inner(ctx, A.Name(name=e.func, pos=e.pos))
            ctx.emit(IRInstr("call", v, [target, *args]))
        else:
            # `overload` extension: sema resolved this call to one specific
            # @overload signature and stamped its real (mangled) compiled
            # symbol here -- jump there instead of the bare, ambiguous name
            # (which was never actually emitted as a real symbol once the
            # source-level defs were each renamed to their own mangled
            # names during sema's overload pre-pass).
            call_target = getattr(e, "resolved_overload_symbol", None) or e.func
            resolved_ov = getattr(e, "resolved_overload_symbol", None)
            if resolved_ov is None and _call_target_is_unresolvable(ctx, e.func):
                # `e.func` names no user function/class, no FFI import, no
                # callable variable, and no builtin this backend models
                # (every modeled builtin -- len/print/str/sorted/... -- is
                # intercepted earlier in this function). It is therefore an
                # unmodeled builtin (`next`/`iter`/`dir`/`delattr`) or a
                # lazily-imported symbol that whole-program merge never
                # compiled in (`from .loader import default_builtins`). Emitting
                # a bare `call e.func` would be an undefined external at link
                # time. Instead evaluate the args for side effects (already
                # done above) and yield 0 -- the SAME graceful "survive
                # unmodeled callables" degradation the opaque-receiver method
                # stub applies, so a build survives code paths it can't model
                # as long as they aren't actually executed at runtime.
                zero_v = ctx.tmp(ir_type_for(A.expr_type(e)))
                ctx.emit(IRInstr("const", zero_v, [0]))
                return zero_v
            ctx.emit(IRInstr("call", v, [call_target, *args]))
        return v

    raise LowerError(f"unsupported expr {type(e).__name__}")


def _call_target_is_unresolvable(ctx: "_FuncCtx", name: str) -> bool:
    """True when a direct `A.Call` target names no symbol this backend can
    ever emit or link -- an unmodeled builtin (`next`/`iter`/`dir`/`delattr`)
    or a lazily-imported name whole-program merge never compiled in
    (`from .loader import default_builtins`). Such a call would otherwise be
    a bare `call name` that fails at link time as an undefined external.

    Conservative by construction: returns True ONLY when `name` is absent
    from every set of resolvable call targets --
    - `func_names`: a real top-level user function (methods are A.MethodCall,
      not A.Call, so they never reach the direct-call fallthrough);
    - `class_names`: a constructor call;
    - `ffi_funcs` / `imported_funcs` / `imported_modules`: an FFI or
      dynamically-imported symbol (these also take dedicated lowering paths
      before the fallthrough, but are checked here for safety);
    - `mlang_code_funcs`: an mlang-compiled Code method.
    Every builtin this backend actually models (len/print/str/sorted/range/
    ...) is intercepted earlier in `_lower_expr_inner` and never reaches the
    fallthrough at all, so a modeled builtin is never mistaken for
    unresolvable here.
    """
    if name in ctx.mctx.func_names or name in ctx.mctx.class_names:
        return False
    if name in ctx.mctx.ffi_funcs or name in ctx.mctx.imported_funcs:
        return False
    if name in ctx.mctx.imported_modules or name in ctx.mctx.mlang_code_funcs:
        return False
    return True


_BOXABLE_STATIC_TYPES = (
    "int", "float", "bool", "str",
    # Containers are boxed too. Unboxed, they reach an "any" slot as bare
    # pointers that `_lower_read_any_tag` cannot tell apart from each other or
    # from a user instance, so type()/isinstance()/str() on an `object`-typed
    # parameter could not answer correctly for them.
    "list", "dict", "tuple", "set",
)


def _none_const(ctx: "_FuncCtx") -> IRValue:
    v = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", v, [0]))
    return v


def _class_tag_key(ctx: "_FuncCtx") -> IRValue:
    sym = ctx.mctx.intern_str("__class__")
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", key_v, [sym]))
    return key_v


def _boxed_value_key(ctx: "_FuncCtx") -> IRValue:
    sym = ctx.mctx.intern_str("__value__")
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", key_v, [sym]))
    return key_v


_DESCRIPTOR_WRAPPERS: dict[str, str] = {
    "staticmethod": "__func__",
    "classmethod": "__func__",
    "property": "fget",
}


def _lower_dynamic_slice_or_index(
    ctx: "_FuncCtx", obj_v: IRValue, idx_v: IRValue, result_ty: str, uid: int
) -> IRValue:
    """`obj[index]` where `index` is opaque: dispatch on the index's runtime
    tag between a slice (`slice(a, b, c)` cell) and an ordinary int element
    load. On the slice branch, read start/stop/step out of the cell and call
    the list-slice helper (the container is a list/tuple at runtime on this
    path -- str slicing goes through the static `s[a:b]` syntax lowering, not
    this dynamic-index path). Falls back to the plain int element load
    otherwise, preserving today's behavior for a genuine integer index.
    """
    maybe_ptr_b = ctx.new_block(f"dynslice_maybeptr_{uid}")
    slice_b = ctx.new_block(f"dynslice_slice_{uid}")
    index_b = ctx.new_block(f"dynslice_index_{uid}")
    end_b = ctx.new_block(f"dynslice_end_{uid}")
    res_ptr = ctx.ensure_slot(f"__dynslice_res_{uid}", PTR if result_ty not in ("float",) else F64)

    # An opaque index is EITHER a raw integer (an ordinary element index) OR
    # a heap-pointer slice cell. Reading a "__class__" tag dereferences the
    # value as a dict header, which would fault on a small raw int. Guard with
    # a low-address check first: only values above the reserved low-address
    # range can be a real heap pointer, so anything at/below it is definitely
    # an integer index and skips the tag read entirely.
    PTR_THRESHOLD = 0x10000
    thresh_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", thresh_v, [PTR_THRESHOLD]))
    looks_ptr = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.gt", looks_ptr, [idx_v, thresh_v]))
    ctx.emit(IRInstr("br.t", None, [looks_ptr, maybe_ptr_b.label, index_b.label]))

    ctx.switch_to(maybe_ptr_b)
    tag_v = _lower_read_any_tag(ctx, idx_v)
    slice_tag = ctx.tmp(I64)
    ctx.emit(IRInstr("const", slice_tag, [BUILTIN_TYPE_IDS["slice"]]))
    is_slice = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_slice, [tag_v, slice_tag]))
    ctx.emit(IRInstr("br.t", None, [is_slice, slice_b.label, index_b.label]))

    ctx.switch_to(slice_b)
    miss = ctx.tmp(I64)
    ctx.emit(IRInstr("const", miss, [0]))
    start_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", start_v, ["_abi_dict_get_default", idx_v, _slice_field_key(ctx, "start"), miss]))
    stop_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", stop_v, ["_abi_dict_get_default", idx_v, _slice_field_key(ctx, "stop"), miss]))
    step_v = ctx.tmp(I64)
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    ctx.emit(IRInstr("call", step_v, ["_abi_dict_get_default", idx_v, _slice_field_key(ctx, "step"), one]))
    sliced = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", sliced, ["_abi_list_slice_step", obj_v, start_v, stop_v, step_v]))
    ctx.emit(IRInstr("store", None, [sliced, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(index_b)
    _emit_list_index_bounds_check(ctx, obj_v, idx_v, uid)
    addr = _list_elem_addr(ctx, obj_v, idx_v)
    elem = ctx.tmp(F64 if result_ty == "float" else I64)
    ctx.emit(IRInstr("load", elem, [addr]))
    ctx.emit(IRInstr("store", None, [elem, res_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(F64 if result_ty == "float" else (PTR if result_ty not in ("int",) else I64))
    ctx.emit(IRInstr("load", out, [res_ptr]))
    return out


def _lower_descriptor_wrapper(ctx: "_FuncCtx", e: A.Call) -> IRValue:
    """`staticmethod(f)` / `classmethod(f)` / `property(fget)`.

    asmpython has no descriptor protocol, but a Python VM written in the
    subset (portapy) uses these builtins as plain markers: it checks
    `isinstance(v, staticmethod)` and reads `v.__func__` / `v.fget`, then
    implements all the actual attribute-get behavior itself. So build a
    tagged cell -- the same instance-shaped `_abi_new_instance` dict a boxed
    scalar / user instance uses -- whose `"__class__"` holds the wrapper's
    BUILTIN_TYPE_IDS tag (so isinstance/type read it) and whose payload key
    (`"__func__"`, plus `"fget"` for property) holds the wrapped callable.
    A zero-arg `property()` (a bare `@property`-less placeholder) stores a
    null payload, matching CPython's `property().fget is None`.
    """
    tag = BUILTIN_TYPE_IDS[e.func]
    payload_key = _DESCRIPTOR_WRAPPERS[e.func]
    func_v = _lower_expr(ctx, e.args[0]) if e.args else _none_const(ctx)
    cell = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cell, ["_abi_new_instance"]))
    tag_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", tag_v, [tag]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, _class_tag_key(ctx), tag_v]))
    key_sym = ctx.mctx.intern_str(payload_key)
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", key_v, [key_sym]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, key_v, func_v]))
    # property also exposes `.fset`/`.fdel` in CPython; store nulls so a VM
    # reading them gets None rather than a missing-key fault. staticmethod/
    # classmethod only need `.__func__`.
    if e.func == "property":
        for extra in ("fset", "fdel"):
            extra_sym = ctx.mctx.intern_str(extra)
            extra_key = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", extra_key, [extra_sym]))
            null_v = _none_const(ctx)
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, extra_key, null_v]))
    return cell


def _slice_field_key(ctx: "_FuncCtx", name: str) -> IRValue:
    sym = ctx.mctx.intern_str(name)
    key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", key_v, [sym]))
    return key_v


def _lower_slice_ctor(ctx: "_FuncCtx", e: A.Call) -> IRValue:
    """`slice(stop)` / `slice(start, stop)` / `slice(start, stop, step)`.

    Builds a tagged cell (`"__class__"` = the slice tag) carrying integer
    "start"/"stop"/"step" bounds, so a Python VM's BUILD_SLICE opcode -- and
    the `obj[slice_obj]` dynamic subscript that consumes it -- work when
    compiled. A missing bound stores the empty-slice sentinel (SENTINEL_MIN
    for start, SENTINEL_MAX for stop, 1 for step) the runtime list/str slice
    helpers already interpret, matching how a literal `xs[a:b]` lowers.
    Mirrors CPython's `slice(stop)` == `slice(None, stop, None)` one-arg form.
    """
    SENTINEL_MIN = -9223372036854775808
    SENTINEL_MAX = 9223372036854775807
    if len(e.args) == 1:
        start_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", start_v, [SENTINEL_MIN]))
        stop_v = _lower_expr(ctx, e.args[0])
        step_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", step_v, [1]))
    else:
        start_v = _lower_expr(ctx, e.args[0])
        stop_v = _lower_expr(ctx, e.args[1])
        if len(e.args) >= 3:
            step_v = _lower_expr(ctx, e.args[2])
        else:
            step_v = ctx.tmp(I64)
            ctx.emit(IRInstr("const", step_v, [1]))
    cell = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cell, ["_abi_new_instance"]))
    tag_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", tag_v, [BUILTIN_TYPE_IDS["slice"]]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, _class_tag_key(ctx), tag_v]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, _slice_field_key(ctx, "start"), start_v]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, _slice_field_key(ctx, "stop"), stop_v]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", cell, _slice_field_key(ctx, "step"), step_v]))
    return cell


def _lower_box_any(ctx: "_FuncCtx", value_v: IRValue, static_ty: str, e: A.Expr | None) -> IRValue:
    """Wrap a concrete scalar value in a tagged cell so its runtime kind
    survives crossing into a genuinely unknown ("any") static type.

    Reuses the exact mechanism a user-class instance already uses to carry
    its own runtime type (an `_abi_new_instance`-allocated dict-shaped cell
    with a reserved "__class__" key -- see the class-construction lowering
    a few hundred lines above this function). A boxed scalar is the same
    shape, with "__class__" holding the value's BUILTIN_TYPE_IDS tag instead
    of a user class id, and a second reserved key "__value__" holding the
    payload (the scalar's raw bits for int/bool, the bitcast pattern for
    float, or the original string pointer for str -- containers/instances
    are NOT boxed here, see the module-level docstring note above
    _lower_expr for why).

    `is_bool_expr`/`is_none_expr` distinguish `bool`/`None` from a plain
    `int` the same way every other type()/isinstance() call site in this
    file already does (there is no separate AST/static type for them --
    both are spelled "int"). `e` may be None when the caller has no AST
    node to consult (already knows the concrete kind some other way); pass
    the real expression whenever one exists so bool/None are tagged
    correctly.
    """
    if static_ty == "int" and e is not None and A.is_none_expr(e):
        # None is already a universal, unambiguous 0 pointer everywhere in
        # the program -- boxing it would make every existing `is None` /
        # null check need to know about boxing too. Leave it as plain 0;
        # the runtime tag reader treats an all-zero pointer as NoneType
        # directly, without needing a cell at all.
        return _none_const(ctx)
    if static_ty == "int" and e is not None and A.is_bool_expr(e):
        tag = BUILTIN_TYPE_IDS["bool"]
        payload = value_v
    elif static_ty == "int":
        tag = BUILTIN_TYPE_IDS["int"]
        payload = value_v
    elif static_ty == "float":
        tag = BUILTIN_TYPE_IDS["float"]
        payload = ctx.tmp(I64)
        ctx.emit(IRInstr("bitcast_f2i", payload, [value_v]))
    elif static_ty == "str":
        tag = BUILTIN_TYPE_IDS["str"]
        payload = value_v
    elif static_ty in ("list", "dict", "tuple", "set"):
        # A container was passed through unboxed on the theory that it "already
        # carries enough runtime shape of its own". It does not: a list, a dict
        # and a user instance are all just pointers, and `_lower_read_any_tag`
        # can only report UNTAGGED for them. So `isinstance(o, dict)` inside
        # `def f(o: object)` answered False for an actual dict, and `str(o)`
        # printed the pointer -- which is why json.dumps({...}) returned
        # "8938224" and pprint rendered {'a': 9920656}.
        #
        # Boxing them makes the tag exact, and BUILTIN_TYPE_IDS already had the
        # ids (-5..-8) waiting. The payload is the container pointer, so
        # `_lower_unbox_any` hands the real container back to every reader.
        tag = BUILTIN_TYPE_IDS[static_ty]
        payload = value_v
    else:
        # An instance, a callable, or a kind with no tag of its own: pass
        # through. A user instance already carries a real "__class__" tag that
        # `_lower_read_any_tag` reads directly.
        return value_v
    # Allocate a dedicated 24-byte BOX cell -- `[BOX_MAGIC][tag][payload]`
    # (see `_abi_new_box` in abi_shims.asm) -- NOT the old dict-shaped
    # `_abi_new_instance`. The magic word at offset 0 lets a reader identify
    # a boxed scalar with one fault-safe load (see `_lower_read_any_tag`),
    # so unboxing/tag-reading is safe on ANY value in an "any" slot,
    # including a raw string/list/dict pointer (which the old dict-probe
    # dereferenced unsafely).
    tag_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", tag_v, [tag]))
    cell = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", cell, ["_abi_new_box", tag_v, payload]))
    return cell


def _is_callable_valued(ctx: "_FuncCtx", e: A.Expr) -> bool:
    """True if `e` evaluates to a FUNCTION POINTER (a lambda literal, or a
    bare reference to a top-level function/class used as a first-class
    value). Such a value must NEVER be boxed as a scalar: sema sometimes
    mis-types an `A.Lambda` as "int" (its body's type leaks out), and boxing
    that "int" would wrap the function pointer in a scalar cell, so a later
    `g(v)` call would invoke the box cell as code and crash. A function
    pointer already IS an opaque pointer value; it crosses into an "any"
    slot fine without boxing (a caller never asks type()/isinstance() of a
    raw callable through the scalar-box path)."""
    if isinstance(e, A.Lambda):
        return True
    if isinstance(e, A.Name):
        return (
            e.name in ctx.mctx.func_names or e.name in ctx.mctx.class_names
        ) and e.name not in ctx.slot_ty
    return False


def _lower_value_into_any_slot(ctx: "_FuncCtx", e: A.Expr) -> IRValue:
    """THE write choke point: lower `e` for storage into a genuinely
    heterogeneous ("any") slot -- a variable, field, parameter, return, or
    `object`-typed container element -- so the slot always holds a BOXED
    value, never a raw one. Store-side half of the uniform tagged-value
    invariant; `_lower_expr` is the read-side half (unboxes an "any" read
    once).

    Cases, by the value's own static type:
    - a concrete boxable scalar (int/float/bool/str) that is NOT a callable:
      box it so its runtime kind survives a later type()/isinstance()/str();
    - an already-"any" value: forward STILL-BOXED via `_lower_expr_inner`
      (NOT `_lower_expr`, which would unbox it on the way in and break the
      invariant);
    - a non-scalar concrete value (list/dict/instance/callable/...): pass
      through unchanged -- it already carries its own runtime shape.

    Only call this for a genuinely "any" destination slot; a precisely typed
    slot stores raw via `_lower_expr` (see `_lower_for_slot`)."""
    static_ty = A.expr_type(e)
    if static_ty in _BOXABLE_STATIC_TYPES and not _is_callable_valued(ctx, e):
        value_v = _lower_expr(ctx, e)
        return _lower_box_any(ctx, value_v, static_ty, e)
    if static_ty == "any":
        return _lower_expr_inner(ctx, e)
    return _lower_expr(ctx, e)


def _lower_for_slot(ctx: "_FuncCtx", e: A.Expr, slot_ty: str) -> IRValue:
    """Lower `e` for storage into a slot of static type `slot_ty`. The single
    entry point every store site (assignment, field set, container element,
    parameter pass, return) routes through, so the box/unbox policy lives in
    ONE place instead of being re-decided inline at each site:

    - slot is "any": go through the write choke point
      (`_lower_value_into_any_slot`) -- the slot must hold a boxed value.
    - slot is concretely typed: lower normally (`_lower_expr`, which unboxes
      an "any"-typed VALUE flowing into a concrete slot -- e.g. assigning an
      `object` return into an `int` variable after a type() narrowing).
    """
    if slot_ty == "any":
        return _lower_value_into_any_slot(ctx, e)
    return _lower_expr(ctx, e)


def _annot_base(annot) -> str:
    """Static base kind ("any"/"int"/"str"/...) of a parameter annotation
    tuple, or "" when unknown/unresolvable (an unannotated param, a callee
    with no recorded signature). "" routes an argument through the ordinary
    `_lower_expr` path -- never wrong, just no boxing."""
    if isinstance(annot, tuple) and len(annot) >= 1 and isinstance(annot[0], str):
        return annot[0]
    return ""


def _callee_param_annots(ctx: "_FuncCtx", func_name: str) -> list:
    """Positional parameter annotation tuples of a statically-known user
    function/method, or [] if unresolvable. Reads `func_param_annots` (raw
    funcdef tuples), NOT FuncSig.param_types (collapsed to base-strings)."""
    annots = getattr(ctx.mctx, "func_param_annots", {})
    return list(annots.get(func_name, []))


def _lower_call_arg(ctx: "_FuncCtx", arg: A.Expr, param_annot) -> IRValue:
    """Lower a positional call argument as a store into the PARAMETER's slot,
    via the `_lower_for_slot` choke point: a concrete scalar into an `any`
    parameter is boxed, an already-`any` argument is forwarded still-boxed,
    an argument into a concretely typed parameter lowers normally. When the
    parameter slot type is unknown, falls back to `_lower_expr`."""
    base = _annot_base(param_annot)
    if base:
        return _lower_for_slot(ctx, arg, base)
    return _lower_expr(ctx, arg)


def _lower_read_any_tag(ctx: "_FuncCtx", obj_v: IRValue) -> IRValue:
    """Runtime kind tag (I64) of a possibly-boxed opaque value.

    Returns: `NONE_TYPE_ID` for a null pointer, the value's `BUILTIN_TYPE_IDS`
    entry for a boxed scalar cell (see `_lower_box_any`), a real (>= 0) user
    class id for a tagged instance (the pre-existing mechanism), or
    `UNTAGGED_ID` for anything else (an ordinary, never-boxed container --
    ordinary because boxing is only applied to scalars, see `_lower_box_any`).

    An ordinary list/tuple/set is NOT dict-shaped, so probing it with
    `_abi_dict_get_default` (a hash lookup that dereferences the dict's
    slot-buffer pointer) would read past the value's real layout and fault.
    A dict/instance cell (from `_abi_new_instance`) and a list share the first
    two header words (cap@0, len@8) but diverge at word 2 (offset 16): a dict
    holds its small TOMBSTONE COUNT there, a list holds its BUFFER POINTER.
    So guard the lookup with a "does word-2 look like a heap pointer" check --
    a real tombstone count is a tiny integer, never a heap address -- and
    report `UNTAGGED_ID` for a list-shaped value instead of dereferencing it
    as a dict. This is the same low-address discriminator the dynamic-slice
    subscript uses for an opaque index.
    """
    PTR_THRESHOLD = 0x10000
    DICT_CAP_INIT = 8  # word-0 of an _abi_new_instance dict cell (DICT_CAP_OFF=8)
    zero = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", zero, [0]))
    none_b = ctx.new_block("anytagnone")
    rawint_b = ctx.new_block("anytagrawint")
    heap_b = ctx.new_block("anytagheap")
    boxcheck_b = ctx.new_block("anytagboxcheck")
    box_b = ctx.new_block("anytagbox")
    instcheck_b = ctx.new_block("anytaginstcheck")
    live_b = ctx.new_block("anytaglive")
    dictish_b = ctx.new_block("anytagdictish")
    end_b = ctx.new_block("anytagend")
    out_ptr = ctx.ensure_slot(f"__anytag_out_{none_b.label}", I64)
    is_none = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_none, [obj_v, zero]))
    ctx.emit(IRInstr("br.t", None, [is_none, none_b.label, heap_b.label]))

    ctx.switch_to(none_b)
    none_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", none_v, [NONE_TYPE_ID]))
    ctx.emit(IRInstr("store", None, [none_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(heap_b)
    # Before dereferencing `obj_v` as a possible boxed cell, require BOTH:
    #   (1) obj_v > PTR_THRESHOLD -- rejects small raw ints;
    #   (2) obj_v is 8-byte aligned -- every real heap object (box/list/dict/
    #       instance/string; all malloc- or zalloc-allocated) is at least
    #       pointer-aligned, so a value with low bits set (a raw int like
    #       0x160e72421ba2) CANNOT be a real object pointer.
    # A raw int that passes (1) but fails (2) reports UNTAGGED instead of
    # faulting on `[obj_v]`. This is what keeps the read-side auto-unbox from
    # crashing on the pointer-sized raw values that flow through an "any"
    # slot in code that hasn't (yet) been made to box at every entry.
    thr0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", thr0, [PTR_THRESHOLD]))
    gt_thr = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.gt", gt_thr, [obj_v, thr0]))
    seven = ctx.tmp(I64)
    ctx.emit(IRInstr("const", seven, [7]))
    low_bits = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", low_bits, [obj_v, seven]))
    zero_lb = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero_lb, [0]))
    aligned = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", aligned, [low_bits, zero_lb]))
    is_heap = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", is_heap, [gt_thr, aligned]))
    ctx.emit(IRInstr("br.t", None, [is_heap, boxcheck_b.label, rawint_b.label]))

    ctx.switch_to(rawint_b)
    rawint_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", rawint_v, [UNTAGGED_ID]))
    ctx.emit(IRInstr("store", None, [rawint_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    # A real heap pointer: is it a BOXED SCALAR cell? Load word-0 (always
    # safe -- every heap object is >= 8 bytes) and compare to BOX_MAGIC. This
    # is the fault-safe discriminator: a raw string/list/dict/instance never
    # has BOX_MAGIC at word 0, so we never dereference one as something it
    # isn't.
    ctx.switch_to(boxcheck_b)
    w0_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", w0_addr, [obj_v, 0]))
    w0 = ctx.tmp(I64)
    ctx.emit(IRInstr("load", w0, [w0_addr]))
    magic_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", magic_v, [BOX_MAGIC]))
    is_box = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_box, [w0, magic_v]))
    ctx.emit(IRInstr("br.t", None, [is_box, box_b.label, instcheck_b.label]))

    ctx.switch_to(box_b)
    tag_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", tag_addr, [obj_v, 8]))  # box tag @8
    boxtag = ctx.tmp(I64)
    ctx.emit(IRInstr("load", boxtag, [tag_addr]))
    ctx.emit(IRInstr("store", None, [boxtag, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    # Not a box: might be a user-class INSTANCE (an _abi_new_instance dict
    # cell carrying a "__class__" id -- the pre-existing tagged-instance
    # mechanism). Only probe it as a dict when it is SHAPED like one: word-0
    # == the dict cell's initial capacity (8) AND word-2 (tomb count) is a
    # small int, never a heap pointer. A raw string/list has neither shape,
    # so it reports UNTAGGED here without ever being dereferenced as a dict.
    ctx.switch_to(instcheck_b)
    notinst_b = ctx.new_block("anytagnotinst")
    # A dict/instance's word-0 is its CAPACITY: 8 initially, DOUBLED on every
    # grow (_runtime_dict_grow: `new_cap = old_cap * 2`), so ALWAYS a power of
    # two >= 8 (8/16/32/64/...). The old check required EXACTLY 8, which
    # wrongly reported UNTAGGED for any instance with enough fields to resize
    # its dict past the initial 8 slots -- e.g. a VM object with a dozen
    # fields -- silently breaking type()/isinstance()/virtual-dispatch on it
    # (the receiver's runtime class id read back as UNTAGGED, so an opaque
    # method call fell through to the graceful 0-stub instead of dispatching).
    # Accept any power-of-two capacity in [8, 2^30] instead. This reads only
    # word-0 (offset 0, always safe); the word-2 tombstone-vs-buffer-pointer
    # guard below still separates a real dict from a list, and boxes were
    # already ruled out above, so it stays fault-safe on a raw string/list.
    cap_ge8 = ctx.tmp(I64)
    eight = ctx.tmp(I64)
    ctx.emit(IRInstr("const", eight, [DICT_CAP_INIT]))
    ctx.emit(IRInstr("icmp.ge", cap_ge8, [w0, eight]))
    cap_max = ctx.tmp(I64)
    ctx.emit(IRInstr("const", cap_max, [1 << 30]))
    cap_le_max = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.le", cap_le_max, [w0, cap_max]))
    # power of two <=> (cap & (cap - 1)) == 0
    one_c = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one_c, [1]))
    cap_minus1 = ctx.tmp(I64)
    ctx.emit(IRInstr("isub", cap_minus1, [w0, one_c]))
    cap_and = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", cap_and, [w0, cap_minus1]))
    zero_c = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero_c, [0]))
    is_pow2 = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_pow2, [cap_and, zero_c]))
    cap_range = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", cap_range, [cap_ge8, cap_le_max]))
    cap_ok = ctx.tmp(I64)
    ctx.emit(IRInstr("iand", cap_ok, [cap_range, is_pow2]))
    ctx.emit(IRInstr("br.t", None, [cap_ok, live_b.label, notinst_b.label]))

    ctx.switch_to(notinst_b)
    notinst_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", notinst_v, [UNTAGGED_ID]))
    ctx.emit(IRInstr("store", None, [notinst_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(live_b)
    # word-2 (offset 16): a dict/instance holds a small TOMBSTONE COUNT there,
    # a list holds its BUFFER POINTER. Guard the dict probe on "word-2 is a
    # small int, not a heap address".
    word2_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", word2_addr, [obj_v, 16]))
    word2 = ctx.tmp(I64)
    ctx.emit(IRInstr("load", word2, [word2_addr]))
    thresh = ctx.tmp(I64)
    ctx.emit(IRInstr("const", thresh, [PTR_THRESHOLD]))
    looks_ptr = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.gt", looks_ptr, [word2, thresh]))
    untagged_b = ctx.new_block("anytaguntagged")
    ctx.emit(IRInstr("br.t", None, [looks_ptr, untagged_b.label, dictish_b.label]))

    ctx.switch_to(untagged_b)
    untagged_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", untagged_v, [UNTAGGED_ID]))
    ctx.emit(IRInstr("store", None, [untagged_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(dictish_b)
    miss_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", miss_v, [UNTAGGED_ID]))
    tag_v = ctx.tmp(I64)
    ctx.emit(IRInstr("call", tag_v, ["_abi_dict_get_default", obj_v, _class_tag_key(ctx), miss_v]))
    ctx.emit(IRInstr("store", None, [tag_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(I64)
    ctx.emit(IRInstr("load", out, [out_ptr]))
    return out


def _lower_unbox_any(ctx: "_FuncCtx", obj_v: IRValue) -> IRValue:
    """Inverse of `_lower_box_any`: given a possibly-boxed opaque `PTR`,
    return the underlying value transparently.

    A cell tagged with a scalar BUILTIN_TYPE_IDS id unwraps to its raw
    payload (bitcast back to float for a float tag). Anything else --
    null, a real instance, an ordinary never-boxed container, or a value
    this function has already unboxed once -- passes through unchanged, so
    calling this on a value that was never boxed is always a safe no-op.
    """
    tag_v = _lower_read_any_tag(ctx, obj_v)
    boxed_b = ctx.new_block("anyunboxboxed")
    float_b = ctx.new_block("anyunboxfloat")
    payload_b = ctx.new_block("anyunboxpayload")
    end_b = ctx.new_block("anyunboxend")
    out_ptr = ctx.ensure_slot(f"__anyunbox_out_{boxed_b.label}", PTR)
    ctx.emit(IRInstr("store", None, [obj_v, out_ptr]))

    float_tag = ctx.tmp(I64)
    ctx.emit(IRInstr("const", float_tag, [BUILTIN_TYPE_IDS["float"]]))
    is_float = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_float, [tag_v, float_tag]))
    is_scalar = ctx.tmp(I64)
    # The boxed tag range is "set"'s id (-8) through "int"'s id (-1): the four
    # scalars AND the four container kinds, all of which `_lower_box_any`
    # wraps in an `_abi_new_box` cell. It deliberately stops at -8 rather than
    # covering the whole table: staticmethod/classmethod/property/slice
    # (-10..-13) are instance-shaped "__class__"-keyed cells, not box cells,
    # so reading a payload at offset 16 would be reading the wrong layout.
    lo_bound = ctx.tmp(I64)
    ctx.emit(IRInstr("const", lo_bound, [BUILTIN_TYPE_IDS["set"]]))
    hi_bound = ctx.tmp(I64)
    ctx.emit(IRInstr("const", hi_bound, [BUILTIN_TYPE_IDS["int"]]))
    ge_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.ge", ge_v, [tag_v, lo_bound]))
    le_v = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.le", le_v, [tag_v, hi_bound]))
    ctx.emit(IRInstr("iand", is_scalar, [ge_v, le_v]))

    ctx.emit(IRInstr("br.t", None, [is_scalar, boxed_b.label, end_b.label]))

    ctx.switch_to(boxed_b)
    ctx.emit(IRInstr("br.t", None, [is_float, float_b.label, payload_b.label]))

    # The box payload lives at offset 16 (`[BOX_MAGIC][tag][payload]`), read
    # with a direct load -- `is_scalar` above already proved (via the
    # BOX_MAGIC check inside `_lower_read_any_tag`) that `obj_v` is a real box
    # cell, so this load is safe.
    ctx.switch_to(payload_b)
    pay_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", pay_addr, [obj_v, 16]))
    raw_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", raw_v, [pay_addr]))
    ctx.emit(IRInstr("store", None, [raw_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(float_b)
    fpay_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", fpay_addr, [obj_v, 16]))
    fraw_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", fraw_v, [fpay_addr]))
    ctx.emit(IRInstr("store", None, [fraw_v, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", out, [out_ptr]))
    return out


def _lower_format_any_value(ctx: "_FuncCtx", val_v: IRValue, repr_mode: bool = False) -> IRValue:
    """Format a possibly-boxed opaque ("any") value to a str, dispatching on
    its runtime tag. `val_v` must be the RAW (still-boxed) cell -- pass the
    `_lower_expr_inner` result.

    A boxed scalar formats from its UNBOXED payload (bool -> "True"/"False",
    int -> decimal, float -> _abi_float_to_str, str -> the string).
    Everything else -- a raw int in an "any" slot, a null (bit-identical to a
    raw 0, left as "0" since a real 0 is far more common at a format site
    than a None), a never-boxed container/instance -- falls back to
    `_abi_fmt_elem(val, 0)`, formatting untagged values exactly as before."""
    tag_v = _lower_read_any_tag(ctx, val_v)
    out_ptr = ctx.ensure_slot(f"__fmtany_out_{id(val_v)}", PTR)
    end_b = ctx.new_block("fmtanyend")
    fallback_b = ctx.new_block("fmtanyfallback")

    def _emit_str_const(text: str) -> None:
        sym = ctx.mctx.intern_str(text)
        v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", v, [sym]))
        ctx.emit(IRInstr("store", None, [v, out_ptr]))

    bool_b = ctx.new_block("fmtanybool")
    after_bool_b = ctx.new_block("fmtanyafterbool")
    bt = ctx.tmp(I64)
    ctx.emit(IRInstr("const", bt, [BUILTIN_TYPE_IDS["bool"]]))
    isb = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", isb, [tag_v, bt]))
    ctx.emit(IRInstr("br.t", None, [isb, bool_b.label, after_bool_b.label]))
    ctx.switch_to(bool_b)
    pb = _lower_unbox_any(ctx, val_v)
    z = ctx.tmp(I64)
    ctx.emit(IRInstr("const", z, [0]))
    istrue = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.ne", istrue, [pb, z]))
    tb = ctx.new_block("fmtanybooltrue")
    fbl = ctx.new_block("fmtanyboolfalse")
    ctx.emit(IRInstr("br.t", None, [istrue, tb.label, fbl.label]))
    ctx.switch_to(tb)
    _emit_str_const("True")
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(fbl)
    _emit_str_const("False")
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(after_bool_b)

    int_b = ctx.new_block("fmtanyint")
    after_int_b = ctx.new_block("fmtanyafterint")
    it = ctx.tmp(I64)
    ctx.emit(IRInstr("const", it, [BUILTIN_TYPE_IDS["int"]]))
    isi = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", isi, [tag_v, it]))
    ctx.emit(IRInstr("br.t", None, [isi, int_b.label, after_int_b.label]))
    ctx.switch_to(int_b)
    ip = _lower_unbox_any(ctx, val_v)
    base_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", base_v, [10]))
    empty_sym = ctx.mctx.intern_str("")
    empty_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", empty_v, [empty_sym]))
    io = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", io, ["_abi_int_to_base", ip, base_v, empty_v]))
    ctx.emit(IRInstr("store", None, [io, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(after_int_b)

    float_b = ctx.new_block("fmtanyfloat")
    after_float_b = ctx.new_block("fmtanyafterfloat")
    ft = ctx.tmp(I64)
    ctx.emit(IRInstr("const", ft, [BUILTIN_TYPE_IDS["float"]]))
    isf = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", isf, [tag_v, ft]))
    ctx.emit(IRInstr("br.t", None, [isf, float_b.label, after_float_b.label]))
    ctx.switch_to(float_b)
    fp = _lower_unbox_any(ctx, val_v)
    fo = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", fo, ["_abi_float_to_str", fp]))
    ctx.emit(IRInstr("store", None, [fo, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(after_float_b)

    str_b = ctx.new_block("fmtanystr")
    after_str_b = ctx.new_block("fmtanyafterstr")
    st = ctx.tmp(I64)
    ctx.emit(IRInstr("const", st, [BUILTIN_TYPE_IDS["str"]]))
    iss = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", iss, [tag_v, st]))
    ctx.emit(IRInstr("br.t", None, [iss, str_b.label, after_str_b.label]))
    ctx.switch_to(str_b)
    sp = _lower_unbox_any(ctx, val_v)
    if repr_mode:
        # repr(str) wraps in single quotes, matching `_lower_expr_as_str`'s
        # own repr path (`'` + text + `'`).
        q_name = ctx.mctx.intern_str("'")
        q = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", q, [q_name]))
        opened = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", opened, ["_abi_str_concat", q, sp]))
        closed = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", closed, ["_abi_str_concat", opened, q]))
        sp = closed
    ctx.emit(IRInstr("store", None, [sp, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))
    ctx.switch_to(after_str_b)

    # Containers are boxed too (see `_lower_box_any`), so their tag is exact
    # here. Format from the UNBOXED pointer through the same runtime repr
    # helpers a statically-typed container uses. The element kind is not
    # knowable at runtime, so each uses the default (integer-ish) element kind
    # -- the same choice an unannotated container makes. Without these arms the
    # fallback below printed the box's ADDRESS, which is what made
    # `json.dumps({...})` return "8938224".
    for _cty, _helper, _extra in (
        ("list", "_abi_list_repr", (0,)),
        ("dict", "_abi_dict_repr", (1, 0)),
        ("set", "_abi_set_repr", (1,)),
    ):
        hit_b = ctx.new_block(f"fmtany{_cty}")
        miss_b = ctx.new_block(f"fmtanyafter{_cty}")
        ct = ctx.tmp(I64)
        ctx.emit(IRInstr("const", ct, [BUILTIN_TYPE_IDS[_cty]]))
        isc = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.eq", isc, [tag_v, ct]))
        ctx.emit(IRInstr("br.t", None, [isc, hit_b.label, miss_b.label]))
        ctx.switch_to(hit_b)
        cptr = _lower_unbox_any(ctx, val_v)
        kinds: list = []
        for _k in _extra:
            kv = ctx.tmp(I64)
            ctx.emit(IRInstr("const", kv, [_k]))
            kinds.append(kv)
        cout = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", cout, [_helper, cptr, *kinds]))
        ctx.emit(IRInstr("store", None, [cout, out_ptr]))
        ctx.emit(IRInstr("br", None, [end_b.label]))
        ctx.switch_to(miss_b)
    # The last container miss falls into the generic fallback.
    ctx.emit(IRInstr("br", None, [fallback_b.label]))

    ctx.switch_to(fallback_b)
    # Unbox before formatting: a tuple (boxed, but with no dedicated repr
    # helper taking a runtime element kind) and every never-boxed value both
    # reach here, and `_lower_unbox_any` is a safe no-op on the latter.
    fb_val = _lower_unbox_any(ctx, val_v)
    k0 = ctx.tmp(I64)
    ctx.emit(IRInstr("const", k0, [0]))
    fbo = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", fbo, ["_abi_fmt_elem", fb_val, k0]))
    ctx.emit(IRInstr("store", None, [fbo, out_ptr]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)
    out = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", out, [out_ptr]))
    return out


def _lower_expr(ctx: "_FuncCtx", e: A.Expr) -> IRValue:
    """THE read choke point: lower `e`, and if it is "any"-typed, unbox a
    boxed scalar cell transparently so consumers (arithmetic, ==, str(),
    print, dict keys, ...) see the real value. The store-side half
    (`_lower_value_into_any_slot` / `_lower_for_slot`) guarantees an "any"
    slot always holds a BOXED value, so this read re-unboxes the same cell
    each time -- never persisting a raw payload a later still-"any" read
    would have to re-derive.

    `_lower_unbox_any` is a true safe no-op on anything that is not a
    positively-magic-tagged box (a raw int below PTR_THRESHOLD, a raw
    string/list/dict/instance pointer, an already-unboxed value): the
    magic-sentinel discriminator (`_abi_new_box` / `_lower_read_any_tag`)
    never dereferences a non-box. So the historical double-unbox segfault
    (`x = maybe(1); y = x`, unboxing an already-raw payload) is now
    harmless -- it just returns the value untouched.

    `type()`/`isinstance()` and the narrowed-name read deliberately call
    `_lower_expr_inner` directly, so they see the still-boxed cell and can
    read its tag.
    """
    v = _lower_expr_inner(ctx, e)
    if A.expr_type(e) == "any" and v.type is PTR:
        return _lower_unbox_any(ctx, v)
    return v


def _for_zip_spec(s: A.For):
    """Recognize `for a, b[, c...] in zip(A, B[, C...])` and
    `for i, (a, b[, c...]) in enumerate(zip(A, B[, C...]))`.

    Returns (idx_name_or_None, names_list, exprs_list) when `s` matches,
    otherwise None. Mirrors sema.py's/codegen.py's own `_for_zip_spec`
    exactly (each stage keeps its own copy rather than sharing one --
    sema only registers TYPES into scope from this shape, it never
    rewrites `s.iter`/`s.targets` into some canonical form, so every
    later stage that needs to recognize `zip()` has to re-detect it from
    the same raw AST shape independently)."""
    it = s.iter
    if it is None or not isinstance(it, A.Call):
        return None
    if it.func == "zip":
        n = len(it.args)
        if n >= 2 and len(s.targets) == n and all(isinstance(t, str) for t in s.targets):
            return (None, list(s.targets), list(it.args))
        return None
    if (
        it.func == "enumerate"
        and len(it.args) == 1
        and isinstance(it.args[0], A.Call)
        and it.args[0].func == "zip"
    ):
        z = it.args[0]
        n = len(z.args)
        if n >= 2 and len(s.targets) == n + 1 and s.targets:
            zip_vars = [s.targets[i] for i in range(1, len(s.targets))]
            return (s.targets[0], zip_vars, list(z.args))
        return None
    return None


def _lower_for_zip(ctx: _FuncCtx, s: A.For, zspec) -> None:
    """`for a, b[, c...] in zip(A, B[, C...])` / `enumerate(zip(...))`.

    Walks N list buffers in lockstep, stopping at the shortest (Python's
    real `zip()` semantics -- silently truncates to the shortest input,
    never errors on a length mismatch). Ports codegen.py's `_gen_for_zip`
    IR-op-for-instruction. Was entirely unimplemented on this backend:
    `A.For`'s only recognized shapes were a plain range()-style loop and
    single-iterable list/enumerate iteration -- a bare `zip()` (with or
    without an enclosing `enumerate`) fell through to `range_args`
    handling, which unconditionally raises on any non-empty
    `s.targets` (`s.targets` here is the parallel-target NAME LIST zip
    loops use, treated by that code as "unsupported tuple-unpack
    targets")."""
    idx_name, znames, zexprs = zspec
    n = len(znames)

    iter_ptrs = [ctx.ensure_slot(f"__zip_{k}_{id(s)}", PTR) for k in range(n)]
    for k, ze in enumerate(zexprs):
        v = _lower_expr(ctx, ze)
        ctx.emit(IRInstr("store", None, [v, iter_ptrs[k]]))

    stop_ptr = ctx.ensure_slot(f"__zip_stop_{id(s)}", I64)
    first_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", first_v, [iter_ptrs[0]]))
    first_len_addr = ctx.tmp(PTR)
    ctx.emit(IRInstr("gep", first_len_addr, [first_v, _LIST_LEN_OFF]))
    stop_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", stop_v, [first_len_addr]))
    ctx.emit(IRInstr("store", None, [stop_v, stop_ptr]))
    for k in range(1, n):
        cur_stop = ctx.tmp(I64)
        ctx.emit(IRInstr("load", cur_stop, [stop_ptr]))
        it_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", it_v, [iter_ptrs[k]]))
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [it_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        is_shorter = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", is_shorter, [len_v, cur_stop]))
        min_ptr = ctx.ensure_slot(f"__zip_min_{k}_{id(s)}", I64)
        shorter_b = ctx.new_block(f"zipmin_shorter_{k}")
        keep_b = ctx.new_block(f"zipmin_keep_{k}")
        after_b = ctx.new_block(f"zipmin_after_{k}")
        ctx.emit(IRInstr("br.t", None, [is_shorter, shorter_b.label, keep_b.label]))
        ctx.switch_to(shorter_b)
        ctx.emit(IRInstr("store", None, [len_v, min_ptr]))
        ctx.emit(IRInstr("br", None, [after_b.label]))
        ctx.switch_to(keep_b)
        ctx.emit(IRInstr("store", None, [cur_stop, min_ptr]))
        ctx.emit(IRInstr("br", None, [after_b.label]))
        ctx.switch_to(after_b)
        new_stop = ctx.tmp(I64)
        ctx.emit(IRInstr("load", new_stop, [min_ptr]))
        ctx.emit(IRInstr("store", None, [new_stop, stop_ptr]))

    i_ptr = ctx.ensure_slot(f"__zip_i_{id(s)}", I64)
    zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", zero, [0]))
    ctx.emit(IRInstr("store", None, [zero, i_ptr]))

    head_b = ctx.new_block("forziphead")
    body_b = ctx.new_block("forzipbody")
    cont_b = ctx.new_block("forzipcont")
    natural_b = ctx.new_block("forzipnatural") if s.orelse else None
    end_b = ctx.new_block("forzipend")
    false_target = natural_b.label if natural_b is not None else end_b.label

    ctx.emit(IRInstr("br", None, [head_b.label]))
    ctx.switch_to(head_b)
    i_v = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v, [i_ptr]))
    stop_v2 = ctx.tmp(I64)
    ctx.emit(IRInstr("load", stop_v2, [stop_ptr]))
    cond = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.lt", cond, [i_v, stop_v2]))
    ctx.emit(IRInstr("br.t", None, [cond, body_b.label, false_target]))

    ctx.switch_to(body_b)
    i_v2 = ctx.tmp(I64)
    ctx.emit(IRInstr("load", i_v2, [i_ptr]))
    for k in range(n):
        it_v2 = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", it_v2, [iter_ptrs[k]]))
        elem_ty = _iter_element_type(zexprs[k])
        addr = _list_elem_addr(ctx, it_v2, i_v2)
        val = ctx.tmp(F64 if elem_ty == "float" else I64)
        ctx.emit(IRInstr("load", val, [addr]))
        _store_loop_target(ctx, znames[k], val, elem_ty)
    if idx_name is not None:
        i_v3 = ctx.tmp(I64)
        ctx.emit(IRInstr("load", i_v3, [i_ptr]))
        _store_loop_target(ctx, idx_name, i_v3, "int")

    ctx.loop_stack.append((cont_b.label, end_b.label))
    for st in s.body:
        _lower_stmt(ctx, st)
    ctx.loop_stack.pop()
    if not ctx.terminated:
        ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    inc_i = ctx.tmp(I64)
    ctx.emit(IRInstr("load", inc_i, [i_ptr]))
    one = ctx.tmp(I64)
    ctx.emit(IRInstr("const", one, [1]))
    next_i = ctx.tmp(I64)
    ctx.emit(IRInstr("iadd", next_i, [inc_i, one]))
    ctx.emit(IRInstr("store", None, [next_i, i_ptr]))
    ctx.emit(IRInstr("br", None, [head_b.label]))

    if natural_b is not None:
        ctx.switch_to(natural_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
        if not ctx.terminated:
            ctx.emit(IRInstr("br", None, [end_b.label]))

    ctx.switch_to(end_b)


def _lower_del_target(ctx: _FuncCtx, tgt: "A.Expr") -> None:
    """Lower one `del` TARGET (a single Name or Subscript -- never a
    TupleLit; the caller unwraps a multi-target `del a, b, c`'s TupleLit
    into one call per element before reaching here). `del x` zeroes the
    slot so a later (illegal, sema should have already rejected it) read
    can't observe a stale value. `del xs[i]` / `del d[key]` calls
    _abi_list_del/_abi_dict_pop, discarding whatever they return --
    matches codegen.py's _gen_stmt Del."""
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


def _lower_stmt(ctx: _FuncCtx, s: A.Stmt) -> None:
    if ctx.terminated:
        # Unreachable: the current block already ended in ret/br/br.t, so this
        # statement cannot execute. `emit` drops individual instructions in
        # that state, but dropping at the INSTRUCTION level is not enough --
        # a statement that needs several blocks (`%` emits a divide-by-zero
        # check, `if`/`while` emit their own) calls `switch_to`, which CLEARS
        # the flag, so the later blocks were emitted for real while the
        # operands they read had been silently dropped. Regalloc then hit a
        # use with no definition and the compiler died with a bare KeyError on
        # the temp name. Concretely, this crashed the build:
        #     for k in range(3):
        #         break
        #         if k % 4 > 2:   # unreachable, but lowered anyway
        #             ...
        # Skipping the whole statement is the same thing CPython's compiler
        # does with unreachable code, and keeps the drop consistent: either
        # all of a statement is lowered or none of it is.
        return
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

    if isinstance(s, A.ClosureBind):
        cap = ctx.tmp(I64)
        ctx.emit(IRInstr("const", cap, [max(2 + len(s.free_vars), 4)]))
        closure = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", closure, ["_abi_new_list", cap]))

        magic = ctx.tmp(I64)
        ctx.emit(IRInstr("const", magic, [0xC105E]))
        ctx.emit(IRInstr("call", None, ["_abi_list_append", closure, magic]))
        function = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", function, [s.func_name]))
        ctx.emit(IRInstr("call", None, ["_abi_list_append", closure, function]))

        nonlocals = set(s.nonlocal_vars)
        for free_name in s.free_vars:
            if free_name in nonlocals:
                box_slot = ctx.ensure_slot(f"__nl_box_{free_name}", PTR)
                if free_name not in ctx.boxed_names:
                    current = _lower_expr(
                        ctx, A.Name(name=free_name, pos=s.pos)
                    )
                    size = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", size, [8]))
                    box = ctx.tmp(PTR)
                    ctx.emit(IRInstr("call", box, ["malloc", size]))
                    ctx.emit(IRInstr("store", None, [current, box]))
                    ctx.emit(IRInstr("store", None, [box, box_slot]))
                    ctx.boxed_names.add(free_name)
                else:
                    box = ctx.tmp(PTR)
                    ctx.emit(IRInstr("load", box, [box_slot]))
                captured = box
            else:
                captured = _lower_expr(
                    ctx, A.Name(name=free_name, pos=s.pos)
                )
            ctx.emit(
                IRInstr(
                    "call",
                    None,
                    ["_abi_list_append", closure, captured],
                )
            )

        destination = _name_ptr(ctx, s.func_name, PTR)
        ctx.emit(IRInstr("store", None, [closure, destination]))
        ctx.closure_names.add(s.func_name)
        ctx.closure_free_counts[s.func_name] = len(s.free_vars)
        return

    if isinstance(s, A.Global) or isinstance(s, A.Nonlocal):
        return

    if isinstance(s, A.Assign):
        if (
            isinstance(s.target, str)
            and isinstance(s.value, A.Call)
            and s.value.func == "import_binary"
        ):
            _lower_import_binary_assign(ctx, s)
            return
        if A.expr_type(s.value).startswith("mlang:"):
            # `code = ml.Code(config, source)`: the RHS is a compile-time-
            # only marker (see the `mlang:` MethodCall case above) --
            # sema.py's _inject_mlang_if_needed already ran the real
            # compiler and recorded the exported signatures keyed by this
            # same uid; `code` itself never becomes a real runtime value
            # (no Code object exists at runtime), so store a harmless
            # null placeholder rather than evaluating `ml.Code(...)` as
            # an ordinary MethodCall (which would incorrectly dispatch
            # through the ffi_funcs/imported_modules machinery for a
            # method that was never a real FFI binding).
            ptr = _name_ptr(ctx, s.target, PTR)
            zero = ctx.tmp(PTR)
            ctx.emit(IRInstr("const", zero, [0]))
            ctx.emit(IRInstr("store", None, [zero, ptr]))
            return
        # Route through the store choke point: an "any"-typed value keeps its
        # boxed form so a later read of this variable re-unboxes the same
        # cell; a concrete value stores raw.
        #
        # The slot's kind is the TARGET's static type, not the value's. For an
        # ANNOTATED binding (`x: int = expr`) the annotation is authoritative:
        # `x: int = some_object_value` must UNBOX the `any` value into the int
        # slot, not keep it boxed. Using the value's own type here (which is
        # "any") would route through `_lower_value_into_any_slot` and store the
        # box pointer raw, so a later `int` read of `x` sees a garbage pointer.
        # A plain (unannotated) binding takes the value's type, as before.
        _annot_slot_ty = _annot_base(getattr(s, "annot", None))
        _slot_ty = _annot_slot_ty if _annot_slot_ty else A.expr_type(s.value)
        val = _lower_for_slot(ctx, s.value, _slot_ty)
        ptr = _name_value_ptr(
            ctx, s.target, ctx.mctx.global_types.get(s.target, val.type)
        )
        ctx.emit(IRInstr("store", None, [val, ptr]))
        if not _is_global_name(ctx, s.target) and A.expr_type(s.value) == "list":
            ctx.slot_el_ty[s.target] = getattr(s.value, "list_el_type", "int")
        if A.expr_type(s.value) == "closure":
            # This variable now holds an escaping closure object (a factory's
            # result). Record it so a later `s.target(...)` call dispatches
            # through the closure object instead of calling the raw object
            # pointer as a function.
            ctx.closure_value_names.add(s.target)
        return

    if isinstance(s, A.MultiAssign):
        # `a = b = c = value` -- CPython evaluates the RHS ONCE and binds every
        # target to that same value, left to right. Mirrors the single-target
        # Assign store above, reusing the one computed value for each name.
        # Was entirely unhandled by this SSA backend (codegen.py had it), so it
        # fell through to the generic "unsupported stmt MultiAssign".
        _ma_ty = A.expr_type(s.value)
        _ma_val = _lower_for_slot(ctx, s.value, _ma_ty)
        for _ma_target in s.targets:
            _ma_ptr = _name_value_ptr(
                ctx, _ma_target, ctx.mctx.global_types.get(_ma_target, _ma_val.type)
            )
            ctx.emit(IRInstr("store", None, [_ma_val, _ma_ptr]))
            if not _is_global_name(ctx, _ma_target) and _ma_ty == "list":
                ctx.slot_el_ty[_ma_target] = getattr(s.value, "list_el_type", "int")
        return

    if isinstance(s, A.AugAssign):
        # `target op= value` -> `target = target op value`, same int/float
        # binop dispatch as a plain BinOp (the target's current static type
        # comes from its existing slot, defaulting to int for a first write
        # -- ensure_slot below mirrors A.Assign's own untyped-slot default).
        cur_ty = ctx.mctx.global_types.get(s.target, ctx.slot_ty.get(s.target, I64))
        ptr = _name_value_ptr(ctx, s.target, cur_ty)
        cur = ctx.tmp(cur_ty)
        ctx.emit(IRInstr("load", cur, [ptr]))
        rhs_ty = A.expr_type(s.value)
        rhs = _lower_expr(ctx, s.value)
        if cur_ty is PTR and rhs_ty.startswith("instance:") and s.op in _AUGASSIGN_DUNDER:
            # `v1 += v2` etc. on a user instance -- was entirely
            # unhandled: AugAssign had no instance-typed dispatch at
            # all, so this fell all the way through to the generic
            # int-BinOp fallback at the bottom, `iadd`-ing the two
            # OBJECT POINTERS together as if they were plain integers.
            # Confirmed via gdb: SIGSEGV, the exact same "raw pointer
            # arithmetic" corruption shape as every other missing-
            # dunder-dispatch bug this session. Checks the in-place
            # dunder (`__iadd__`) first (CPython precedence: in-place
            # mutates the receiver and returns it, or another object,
            # which is REBOUND to the target either way -- Python
            # itself always re-assigns after an augmented op, even for
            # `__iadd__`, since e.g. `int.__iadd__` doesn't exist and
            # falls back to `__add__`'s fresh-object return), falling
            # back to the plain dunder (`__add__`) if no in-place
            # override exists. Derives the class name from the RHS's
            # own resolved type rather than the target's -- `s.target`
            # is a bare string with no cached `inferred_type` of its
            # own to query (unlike every other dunder-dispatch site in
            # this file, which reads a real AST expression node); this
            # is exactly right for the overwhelmingly common real case
            # (`v1 += v2` where both sides share a class) and doesn't
            # regress anything that was unsupported a moment ago.
            inplace_dunder, dunder = _AUGASSIGN_DUNDER[s.op]
            cls_name = rhs_ty.split(":", 1)[1]
            owner = _resolve_method_owner(ctx, cls_name, inplace_dunder)
            method = inplace_dunder
            if owner is None:
                owner = _resolve_method_owner(ctx, cls_name, dunder)
                method = dunder
            if owner is not None:
                res = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", res, [f"{owner}__{method}", cur, rhs]))
                ctx.emit(IRInstr("store", None, [res, ptr]))
                return
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
        elif s.op == "**":
            # `x **= n` for ints: no single IR op, reuse the same
            # non-negative-exponent multiply loop the `**` BinOp / pow() use.
            res = _lower_int_pow(ctx, cur, rhs, id(s))
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
            slot_vt = getattr(target.obj, "value_type", "int")
            if slot_vt == "any":
                # `d[k] = value` into a genuinely heterogeneous
                # `dict[str, object]`: route through the store choke point so
                # a concrete scalar is boxed AND an already-"any" value (a
                # boxed cell forwarded from elsewhere, e.g. a `v: object`
                # parameter) stays boxed. A homogeneous `dict[str, int]` has
                # slot_vt "int" (not "any") and skips this. Previously this
                # only fired for a concrete-scalar value, so forwarding an
                # already-boxed `any` value took the else branch and
                # `_lower_expr` UNBOXED it on the way in -- storing a raw
                # payload a later `type(d[k])` then couldn't classify.
                val = _lower_value_into_any_slot(ctx, s.value)
            else:
                val = _lower_expr(ctx, s.value)
                if A.expr_type(s.value) == "float":
                    # Same int-only-cell constraint as A.AttrAssign/A.ListLit.
                    iv = ctx.tmp(I64)
                    ctx.emit(IRInstr("bitcast_f2i", iv, [val]))
                    val = iv
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_v, val]))
            return
        setitem_cls = getattr(target, "_setitem_class", "")
        if setitem_cls or obj_ty.startswith("instance:"):
            # `dd[key] = value` on a user instance with __setitem__ (sema
            # already validated this and stamped `_setitem_class` on the
            # target Subscript) -- mirrors A.Subscript's own __getitem__
            # dispatch just above in this file (`f"{cls_name}____getitem__"`).
            cls_name = setitem_cls or obj_ty.split(":", 1)[1]
            obj_v = _lower_expr(ctx, target.obj)
            idx_v = _lower_expr(ctx, target.index)
            val = _lower_expr(ctx, s.value)
            ctx.emit(IRInstr("call", None, [f"{cls_name}____setitem__", obj_v, idx_v, val]))
            return
        if obj_ty == "outparam":
            # `out[i] = value`: sema already type-checked `value` against
            # the declared pointee kind. `target.obj`'s own IRValue already
            # IS the raw pointer -- an exported function's outparam[T]
            # parameter is lowered PTR-typed (ir_type_for's default for any
            # annotation base it doesn't recognize as a by-value scalar)
            # and passed through unmodified, unlike an ordinary local which
            # gets an extra stack slot. `val`'s own IRValue.type from
            # _lower_expr already picks the right store width for the
            # common int/float single-pointee case ("store"'s codegen
            # branches on it: f64 -> movsd, i64 -> mov) -- an int literal/
            # expression already comes out I64-typed and a float one
            # F64-typed, matching outparam[int]/outparam[float]'s real ABI
            # pointee width with no bitcast needed (unlike list/dict
            # cells, which are always raw i64 storage regardless of
            # element kind). outparam[int8] (a byte-granularity buffer,
            # e.g. `uint8_t *buffer`) needs real index*1 address arithmetic
            # and an 8-bit-wide store instead.
            el_kind = ctx.slot_el_ty.get(target.obj.name, "int") if isinstance(target.obj, A.Name) else "int"
            ptr_v = _lower_expr(ctx, target.obj)
            idx_v = _lower_expr(ctx, target.index)
            val = _lower_expr(ctx, s.value)
            elem_size = _INPARAM_OUTPARAM_ELEM_SIZE.get(el_kind, 8)
            addr = _inparam_elem_addr(ctx, ptr_v, idx_v, elem_size=elem_size)
            if el_kind == "int8":
                truncated = ctx.tmp(U8)
                ctx.emit(IRInstr("trunc", truncated, [val]))
                ctx.emit(IRInstr("store", None, [truncated, addr]))
            elif el_kind == "int32":
                truncated = ctx.tmp(I32)
                ctx.emit(IRInstr("trunc", truncated, [val]))
                ctx.emit(IRInstr("store", None, [truncated, addr]))
            else:
                ctx.emit(IRInstr("store", None, [val, addr]))
            return
        if obj_ty == "any":
            # `x[k] = v` where x's static type is opaque ("any") -- x may at
            # runtime be a dict/set (`_abi_new_instance` shape) or a
            # list/tuple. Dispatch on the runtime shape, mirroring how the
            # membership/slice `any` paths discriminate: a dict/set holds a
            # small TOMBSTONE COUNT at word-2 (offset 16), a list its BUFFER
            # POINTER, so "word-2 is a small int, not a heap address" selects
            # the dict path. The value is boxed via the store choke point in
            # both arms so its runtime kind survives a later `x[k]` read that
            # auto-unboxes (a raw list slot holds the box pointer fine). Was a
            # hard "unsupported stmt IndexAssign (any)".
            obj_v = _lower_expr(ctx, target.obj)
            val = _lower_value_into_any_slot(ctx, s.value)
            obj_slot = ctx.ensure_slot(f"__idxasany_obj_{id(s)}", PTR)
            val_slot = ctx.ensure_slot(f"__idxasany_val_{id(s)}", val.type)
            ctx.emit(IRInstr("store", None, [obj_v, obj_slot]))
            ctx.emit(IRInstr("store", None, [val, val_slot]))

            PTR_THRESHOLD = 0x10000
            w2_addr = ctx.tmp(PTR)
            ctx.emit(IRInstr("gep", w2_addr, [obj_v, 16]))
            w2 = ctx.tmp(I64)
            ctx.emit(IRInstr("load", w2, [w2_addr]))
            thr = ctx.tmp(I64)
            ctx.emit(IRInstr("const", thr, [PTR_THRESHOLD]))
            w2_is_ptr = ctx.tmp(I64)
            ctx.emit(IRInstr("icmp.gt", w2_is_ptr, [w2, thr]))

            list_b = ctx.new_block("idxasanylist")
            dict_b = ctx.new_block("idxasanydict")
            end_b = ctx.new_block("idxasanyend")
            ctx.emit(IRInstr("br.t", None, [w2_is_ptr, list_b.label, dict_b.label]))

            ctx.switch_to(dict_b)
            d_obj = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", d_obj, [obj_slot]))
            key_v = _lower_dict_key(ctx, target.index)
            d_val = ctx.tmp(val.type)
            ctx.emit(IRInstr("load", d_val, [val_slot]))
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", d_obj, key_v, d_val]))
            ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(list_b)
            l_obj = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", l_obj, [obj_slot]))
            l_idx = _lower_expr(ctx, target.index)
            l_addr = _list_elem_addr(ctx, l_obj, l_idx)
            l_val = ctx.tmp(val.type)
            ctx.emit(IRInstr("load", l_val, [val_slot]))
            ctx.emit(IRInstr("store", None, [l_val, l_addr]))
            ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(end_b)
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
        if len(s.values) == 1 and A.expr_type(s.values[0]) in (
            "list",
            "tuple",
            "any",
            # Unannotated values retain the historical "int" sentinel even
            # when their runtime value is an iterable. Sema has already
            # accepted this unpack, so lower it through the ordinary
            # list/tuple cell layout just like "any".
            "int",
        ):
            # Single-iterable unpack (a, b = some_tuple_or_list_expr).
            # Each target takes its element's REAL type: a tuple RHS carries
            # per-index kinds in `tuple_elem_types` (e.g. `(y, i, q)` of floats),
            # a list RHS shares one homogeneous element type. Loading every
            # element as I64 -- the old behavior -- reinterpreted a float
            # element's bits as an int for the target slot, so the first target
            # (whose slot happened to line up) read right but any float target
            # after it came back as garbage. Mirrors the starred-unpack arm
            # above (`ir_type_for(el_ty)` load + `_store_loop_target`).
            src_v = _lower_expr(ctx, s.values[0])
            rhs_t = A.expr_type(s.values[0])
            uniform_el = _iter_element_type(s.values[0]) if rhs_t == "list" else None
            elem_types = getattr(s.values[0], "tuple_elem_types", [])
            for i, name in enumerate(names):
                idx_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", idx_v, [i]))
                addr = _list_elem_addr(ctx, src_v, idx_v)
                if uniform_el is not None:
                    el_ty = uniform_el
                elif i < len(elem_types):
                    el_ty = elem_types[i]
                else:
                    # No tracked element type (unannotated iterable, the "int"
                    # sentinel path): keep the historical int-shaped load.
                    el_ty = "int"
                val = ctx.tmp(ir_type_for(el_ty))
                ctx.emit(IRInstr("load", val, [addr]))
                _store_loop_target(ctx, name, val, el_ty)
            return
        raise LowerError(
            f"unsupported stmt TupleAssign (shape) at {s.pos}: "
            f"targets={s.targets!r}, values={s.values!r}"
        )

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
        if isinstance(s.obj, A.Name) and s.obj.name in ctx.mctx.class_names:
            # A write through a class object to an attribute not declared
            # directly in that class creates a mutable subclass attribute.
            label = ctx.mctx.class_object_labels[s.obj.name]
            class_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", class_ptr, [label]))
            class_object = ctx.tmp(PTR)
            ctx.emit(IRInstr("load", class_object, [class_ptr]))
            key_name = ctx.mctx.intern_str(s.name)
            key = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", key, [key_name]))
            val = _lower_expr(ctx, s.value)
            if A.expr_type(s.value) == "float":
                bits = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", bits, [val]))
                val = bits
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", class_object, key, val]))
            return
        if A.expr_type(s.obj) == "type" and ctx.mctx.class_ids:
            # `cls.attr = value` where the class isn't a literal name -- a class
            # object flowing through a variable/param (e.g. a registry's
            # `def register(self, name, cls): cls.__somnia_type__ = name`). The
            # receiver is the class's RTTI id, not an instance dict; storing
            # through the generic instance path below would `_abi_dict_set` on
            # that small integer and fault. Dispatch on the runtime id to the
            # matching class's mutable namespace (`__classobj_<owner>`), the
            # same per-class dict the literal `ClassName.attr = v` branch writes.
            recv_v = _lower_expr(ctx, s.obj)  # the class id
            key_name = ctx.mctx.intern_str(s.name)
            val = _lower_expr(ctx, s.value)
            if A.expr_type(s.value) == "float":
                bits = ctx.tmp(I64)
                ctx.emit(IRInstr("bitcast_f2i", bits, [val]))
                val = bits
            rows = sorted(ctx.mctx.class_ids.items(), key=lambda kv: kv[1])
            check_blocks = [ctx.new_block(f"clsattrset_chk{i}") for i in range(len(rows))]
            hit_blocks = [ctx.new_block(f"clsattrset_hit{i}") for i in range(len(rows))]
            end_b = ctx.new_block("clsattrset_end")
            ctx.emit(IRInstr("br", None, [check_blocks[0].label]))
            for i, (cname, cid) in enumerate(rows):
                ctx.switch_to(check_blocks[i])
                cid_v = ctx.tmp(I64)
                ctx.emit(IRInstr("const", cid_v, [cid]))
                is_match = ctx.tmp(I64)
                ctx.emit(IRInstr("icmp.eq", is_match, [recv_v, cid_v]))
                next_label = check_blocks[i + 1].label if i + 1 < len(check_blocks) else end_b.label
                ctx.emit(IRInstr("br.t", None, [is_match, hit_blocks[i].label, next_label]))

                ctx.switch_to(hit_blocks[i])
                label = ctx.mctx.class_object_labels[cname]
                class_ptr = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", class_ptr, [label]))
                class_object = ctx.tmp(PTR)
                ctx.emit(IRInstr("load", class_object, [class_ptr]))
                key = ctx.tmp(PTR)
                ctx.emit(IRInstr("global_addr", key, [key_name]))
                ctx.emit(IRInstr("call", None, ["_abi_dict_set", class_object, key, val]))
                ctx.emit(IRInstr("br", None, [end_b.label]))
            ctx.switch_to(end_b)
            return
        # obj.name = value -> _abi_dict_set(obj, name, value); see the
        # A.Attr read path's comment for why this goes through a shim.
        obj_val = _lower_expr(ctx, s.obj)
        name = ctx.mctx.intern_str(s.name)
        key_ptr = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", key_ptr, [name]))
        # Store choke point: an "any"-typed field value stays boxed so a later
        # `obj.name` read re-unboxes the same cell.
        val = _lower_for_slot(ctx, s.value, A.expr_type(s.value))
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
        elif ctx.ret_ty == "float" and A.expr_type(s.value) == "int":
            # A `-> float` function returning an int-typed expression from
            # THIS branch (e.g. statistics.median()'s odd-length
            # `return sorted_data[n // 2]`) needs an explicit sitofp -- the
            # raw int bits would otherwise be read back as a float bit
            # pattern with no conversion. Reconciled here, at the return
            # site, the one place the function's unified type is known.
            ret_val = _lower_expr(ctx, s.value)
            fv = ctx.tmp(F64)
            ctx.emit(IRInstr("sitofp", fv, [ret_val]))
            ret_val = fv
        else:
            # The return is a store into a slot of type `ctx.ret_ty`: route
            # through the store choke point. When the function returns "any",
            # this boxes a concrete-this-branch scalar (so the caller, who
            # only knows the function returns "any", can still recover the
            # kind) and forwards an already-"any" value still-boxed --
            # UNCONDITIONALLY, replacing the old conservative
            # `box_any_returns` heuristic (which under-boxed when sema had
            # unified the function's return to a single concrete type while a
            # branch still yielded a differently-typed value).
            ret_val = _lower_for_slot(ctx, s.value, ctx.ret_ty)
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

        narrowed = _narrowed_if_type(ctx, s.test)

        ctx.switch_to(then_b)
        if narrowed is not None:
            name, ty = narrowed
            outer_ty = ctx.narrowed_types.get(name)
            outer_cache = ctx.narrowed_cache.get(name)
            ctx.narrowed_types[name] = ty
            ctx.narrowed_cache.pop(name, None)
        for st in s.then:
            _lower_stmt(ctx, st)
        if narrowed is not None:
            name = narrowed[0]
            if outer_ty is None:
                ctx.narrowed_types.pop(name, None)
            else:
                ctx.narrowed_types[name] = outer_ty
            if outer_cache is None:
                ctx.narrowed_cache.pop(name, None)
            else:
                ctx.narrowed_cache[name] = outer_cache
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
        iter_is_instance = getattr(s, "iter_is_instance", None)
        if iter_is_instance is not None:
            _lower_for_iter_protocol(ctx, s, iter_is_instance)
            return
        zspec = _for_zip_spec(s)
        if zspec is not None:
            _lower_for_zip(ctx, s, zspec)
            return
        _enum_start_kwarg = None
        for _kn, _kv in getattr(s.iter, "kwargs", None) or []:
            if _kn == "start":
                _enum_start_kwarg = _kv
        if (
            s.targets
            and isinstance(s.iter, A.Call)
            and s.iter.func == "enumerate"
            and len(s.iter.args) in (1, 2)
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
            # `enumerate(xs, start)` -- the index target counts up from
            # `start` instead of 0 (a separate counter from the internal
            # array-position index driving the loop condition/element
            # reads below, which must still start at 0 regardless of
            # `start`). `_start_arg` accepts either the 2nd positional
            # arg or a `start=` keyword, matching sema's own
            # `_enum_start_kwarg`/`_enum_n_args` handling exactly. This
            # whole enumerate-with-start shape was previously entirely
            # unrecognized (the `len(s.iter.args) == 1` guard rejected
            # it outright), falling through to the generic For handler's
            # unconditional "iterating 'int'" error on `s.range_args`.
            _start_arg = s.iter.args[1] if len(s.iter.args) == 2 else _enum_start_kwarg
            display_idx_ptr = ctx.ensure_slot(f"__for_display_idx_{id(s)}", I64)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            ctx.emit(IRInstr("store", None, [zero, idx_ptr]))
            if _start_arg is not None:
                start_v = _lower_expr(ctx, _start_arg)
                ctx.emit(IRInstr("store", None, [start_v, display_idx_ptr]))
            else:
                ctx.emit(IRInstr("store", None, [zero, display_idx_ptr]))

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
            body_display_idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", body_display_idx_v, [display_idx_ptr]))
            _store_loop_target(ctx, s.targets[0], body_display_idx_v, "int")
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
            inc_display_idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("load", inc_display_idx_v, [display_idx_ptr]))
            one2 = ctx.tmp(I64)
            ctx.emit(IRInstr("const", one2, [1]))
            next_display_idx_v = ctx.tmp(I64)
            ctx.emit(IRInstr("iadd", next_display_idx_v, [inc_display_idx_v, one2]))
            ctx.emit(IRInstr("store", None, [next_display_idx_v, display_idx_ptr]))
            ctx.emit(IRInstr("br", None, [head_b.label]))

            if natural_b is not None:
                ctx.switch_to(natural_b)
                for st in s.orelse:
                    _lower_stmt(ctx, st)
                ctx.emit(IRInstr("br", None, [end_b.label]))

            ctx.switch_to(end_b)
            return
        iter_t = A.expr_type(s.iter)
        if iter_t not in ("list", "tuple", "dict", "set", "str", "any", "int"):
            raise LowerError(f"unsupported stmt For (iterating {iter_t!r})")
        if iter_t in ("dict", "set"):
            el_ty = "str"
        elif iter_t == "str":
            el_ty = "str"
        elif iter_t in ("any", "int"):
            el_ty = "any"
        elif iter_t == "tuple":
            tuple_types = A.tuple_element_types(s.iter)
            el_ty = tuple_types[0] if tuple_types else "int"
        elif isinstance(s.iter, A.ListLit):
            el_ty = s.iter.el_type
        elif isinstance(s.iter, A.Name):
            el_ty = ctx.slot_el_ty.get(
                s.iter.name,
                ctx.mctx.global_list_el_ty.get(s.iter.name, "int"),
            )
        else:
            el_ty = getattr(s.iter, "list_el_type", "int") or "int"
        var_ty = PTR if s.targets else ir_type_for(el_ty)

        if iter_t in ("dict", "set"):
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
        # `del a, b, c` -> the parser wraps multiple targets in a
        # TupleLit (see _parse_del's own comment for why); each element
        # is a fully independent deletion, lowered one at a time.
        if isinstance(s.target, A.TupleLit):
            for elem in s.target.elems:
                _lower_del_target(ctx, elem)
        else:
            _lower_del_target(ctx, s.target)
        return

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
        # Extract or synthesize the message string. `raise UserExcClass(arg)`
        # for a user-defined exception class: the constructor's first arg IS
        # the message regardless of its static type (matches codegen.py's
        # own _cg_is_exception_class handling exactly -- e.g. subprocess.
        # CalledProcessError(returncode, cmd)'s first arg is an int, not a
        # str; `str(exc)` on the caught instance still prints "1", not "").
        # Non-str/int/float first-arg types (list/dict/etc) fall back to
        # _lower_expr_as_str's own general coercion rather than codegen.py's
        # dedicated container-placeholder message -- an acceptable smaller
        # gap on this backend, not yet exercised by any test case.
        if isinstance(s.value, A.Call) and s.value.args:
            msg_v = _lower_expr_as_str(ctx, s.value.args[0])
        elif A.expr_type(s.value) == "str":
            msg_v = _lower_expr(ctx, s.value)
        else:
            empty = ctx.mctx.intern_str("")
            msg_v = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", msg_v, [empty]))
        ctx.emit(IRInstr("call", None, ["_abi_raise", msg_v, exc_id_v]))


def _lower_for_iter_protocol(ctx: _FuncCtx, s: A.For, cls_name: str) -> None:
    """`for x in obj` where obj is a user class defining __iter__/__next__
    (sema stamps `s.iter_is_instance = cls_name` after validating both
    methods exist -- see sema.py's A.For handling). Was entirely
    unimplemented: fell through to the generic For fallback's
    LowerError, since `A.expr_type(s.iter)` is `"instance:X"`, not one
    of the list/dict/str/any shapes that fallback accepts.

    Lowers exactly like codegen.py's `_gen_for_iter`:
        iterator = obj.__iter__()
        loop:
            setjmp(buf) -- 0 normally, nonzero after a longjmp
            if exception: StopIteration -> end; else re-raise
            var = iterator.__next__()
            body
            jmp loop
        end:
    Ported as IR blocks + the same `_abi_setjmp`/`_abi_raise`/
    `_runtime_handler_top` primitives `_lower_try` already uses. One
    `try_regions` entry per setjmp installation so regalloc.py's
    false-loop-detection exclusion (`_last_uses`'s try_regions-based
    fix, see its own docstring) covers this construct's backward
    branches too -- otherwise the same false "loop over the whole
    try/except" liveness bug that fix addresses would misfire on the
    real, genuine loop this statement also contains.
    """
    uid = id(s)
    owner = _resolve_method_owner(ctx, cls_name, "__iter__") or cls_name
    next_owner = _resolve_method_owner(ctx, cls_name, "__next__") or cls_name

    obj_v = _lower_expr(ctx, s.iter)
    iter_ptr = ctx.ensure_slot(f"__for_iter_obj_{uid}", PTR)
    ctx.emit(IRInstr("store", None, [obj_v, iter_ptr]))
    iter_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", iter_v, [f"{owner}____iter__", obj_v]))
    ctx.emit(IRInstr("store", None, [iter_v, iter_ptr]))

    buf_ptr = ctx.raw_slot(f"__for_iter_buf_{uid}", _JMP_BUF_SIZE)
    parent_ptr = ctx.ensure_slot(f"__for_iter_parent_{uid}", PTR)
    prev_msg_ptr = ctx.ensure_slot(f"__for_iter_prev_msg_{uid}", PTR)
    prev_type_ptr = ctx.ensure_slot(f"__for_iter_prev_type_{uid}", I64)

    top_b = ctx.new_block(f"for_iter_top_{uid}")
    handler_b = ctx.new_block(f"for_iter_handler_{uid}")
    body_b = ctx.new_block(f"for_iter_body_{uid}")
    cont_b = ctx.new_block(f"for_iter_cont_{uid}")
    end_b = ctx.new_block(f"for_iter_end_{uid}")
    natural_b = ctx.new_block(f"for_iter_natural_{uid}") if s.orelse else None
    ctx.emit(IRInstr("br", None, [top_b.label]))

    ctx.switch_to(top_b)
    cur_msg = _load_global(ctx, "_runtime_exc_msg", PTR)
    ctx.emit(IRInstr("store", None, [cur_msg, prev_msg_ptr]))
    cur_type = _load_global(ctx, "_runtime_exc_type", I64)
    ctx.emit(IRInstr("store", None, [cur_type, prev_type_ptr]))
    cur_top = _load_global(ctx, "_runtime_handler_top", PTR)
    ctx.emit(IRInstr("store", None, [cur_top, parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", buf_ptr)

    setjmp_block_label = ctx.cur.label
    setjmp_result = ctx.tmp(I64)
    ctx.emit(IRInstr("call", setjmp_result, ["_abi_setjmp", buf_ptr]))
    ctx.emit(IRInstr("br.t", None, [setjmp_result, handler_b.label, body_b.label]))

    ctx.switch_to(body_b)
    # Handler must stay INSTALLED across the __next__ call -- that's the
    # call that can raise StopIteration -- and only get restored to the
    # parent AFTER it returns normally. Mirrors codegen.py's
    # `_gen_for_iter`: the handler-chain restore happens right after
    # `emit_call(__next__)`, not before. (Confirmed via a real repro:
    # restoring the handler chain BEFORE the __next__ call means
    # _runtime_handler_top is NULL/back-to-the-outer-scope by the time
    # __next__ actually raises, so `raise StopIteration(...)` inside it
    # finds no installed handler at all and terminates the process
    # instead of being caught here.)
    cur_iter = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", cur_iter, [iter_ptr]))
    # Element type comes from __next__'s own declared return annotation
    # (mirrors sema.py's identical `next_sig.ret_type[0]` lookup when it
    # stamped `s.iter_is_instance` -- that's how it typed `s.var` in
    # scope in the first place, so re-deriving it the same way here
    # keeps the two in agreement).
    next_sig = ctx.mctx.classes_sig.get(next_owner)
    next_msig = next_sig.methods.get("__next__") if next_sig is not None else None
    el_kind = "any"
    if next_msig is not None and getattr(next_msig, "ret_type", None):
        el_kind = next_msig.ret_type[0]
    next_v = ctx.tmp(ir_type_for(el_kind))
    ctx.emit(IRInstr("call", next_v, [f"{next_owner}____next__", cur_iter]))
    parent_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", parent_v, [parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", parent_v)
    _store_loop_target(ctx, s.var, next_v, el_kind)

    ctx.loop_stack.append((cont_b.label, end_b.label))
    for st in s.body:
        _lower_stmt(ctx, st)
    ctx.loop_stack.pop()
    ctx.emit(IRInstr("br", None, [cont_b.label]))

    ctx.switch_to(cont_b)
    ctx.emit(IRInstr("br", None, [top_b.label]))

    ctx.switch_to(handler_b)
    parent_v2 = ctx.tmp(PTR)
    ctx.emit(IRInstr("load", parent_v2, [parent_ptr]))
    _store_global(ctx, "_runtime_handler_top", parent_v2)
    exc_type_v = _load_global(ctx, "_runtime_exc_type", I64)
    stop_iter_v = ctx.tmp(I64)
    ctx.emit(IRInstr("const", stop_iter_v, [BUILTIN_EXC_IDS["StopIteration"]]))
    is_stop = ctx.tmp(I64)
    ctx.emit(IRInstr("icmp.eq", is_stop, [exc_type_v, stop_iter_v]))
    reraise_b = ctx.new_block(f"for_iter_reraise_{uid}")
    ctx.emit(IRInstr("br.t", None, [is_stop, (natural_b or end_b).label, reraise_b.label]))

    ctx.switch_to(reraise_b)
    reraise_msg = _load_global(ctx, "_runtime_exc_msg", PTR)
    ctx.emit(IRInstr("call", None, ["_abi_raise", reraise_msg, exc_type_v]))
    ctx.emit(IRInstr("br", None, [end_b.label]))

    if natural_b is not None:
        ctx.switch_to(natural_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
        ctx.emit(IRInstr("br", None, [end_b.label]))

    _record_try_region(ctx, setjmp_block_label)
    ctx.switch_to(end_b)


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
    setjmp_block_label = ctx.cur.label
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
    _record_try_region(ctx, setjmp_block_label)

    ctx.switch_to(end_b)


def _collect_return_values(stmts: list, out: list) -> None:
    """Recursively gather every `A.Return`'s value expression reachable
    from `stmts`, at any control-flow nesting depth. Self-contained (not
    shared with sema.py's own `_collect_returns`, a different module) --
    only needs enough coverage to find every return in a function body,
    which the same statement-container attribute set every other
    recursive-statement-walker in this file already uses (`then`/`orelse`/
    `body`/`handler`/`else_body`/`finally_body` + `A.Try.extra_handlers`)
    provides."""
    for s in stmts:
        if isinstance(s, A.Return):
            if s.value is not None:
                out.append(s.value)
            continue
        for name in ("then", "orelse", "body", "handler", "else_body", "finally_body"):
            nested = getattr(s, name, None)
            if isinstance(nested, list):
                _collect_return_values(nested, out)
        if isinstance(s, A.Try):
            for _types, _binding, body in s.extra_handlers:
                _collect_return_values(body, out)


def _has_genuinely_heterogeneous_returns(body: list) -> bool:
    """True if this function's own `return` statements provably disagree
    on concrete type -- i.e. at least two distinct, non-"any" static types
    appear among them. See `_FuncCtx.box_any_returns`'s docstring for why
    this conservative guard exists."""
    values: list = []
    _collect_return_values(body, values)
    concrete_types = {A.expr_type(v) for v in values}
    concrete_types.discard("any")
    return len(concrete_types) >= 2


def lower_func(
    f: A.FuncDef,
    mctx: _ModuleCtx,
    *,
    visibility: str | None = None,
    module_body: bool = False,
) -> IRFunc:
    declared_globals: set[str] = set()
    _collect_declared_globals(f.body, declared_globals)
    declared_nonlocals: set[str] = set()
    _collect_declared_nonlocals(f.body, declared_nonlocals)
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
    ctx.nonlocal_names = declared_nonlocals
    owner_class = getattr(f, "method_owner_class", None)
    if owner_class is not None:
        ctx.method_owner_class = owner_class
        # A non-static method's first parameter is its receiver (self/cls);
        # a staticmethod has no implicit receiver.
        if "staticmethod" not in getattr(f, "decorators", []) and f.params:
            ctx.receiver_param = f.params[0]
    if isinstance(f.ret_type, tuple) and f.ret_type:
        ctx.ret_ty = f.ret_type[0]
    if ctx.ret_ty == "any":
        ctx.box_any_returns = _has_genuinely_heterogeneous_returns(f.body)
    entry = ctx.new_block("entry")
    ctx.switch_to(entry)
    ctx.shared_zero = ctx.tmp(I64)
    ctx.emit(IRInstr("const", ctx.shared_zero, [0]))
    if module_body:
        for label in ctx.mctx.class_object_labels.values():
            class_object = ctx.tmp(PTR)
            ctx.emit(IRInstr("call", class_object, ["_abi_new_instance"]))
            destination = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", destination, [label]))
            ctx.emit(IRInstr("store", None, [class_object, destination]))

    params: list[IRValue] = []
    for i, pname in enumerate(f.params):
        annot = f.param_types[i] if i < len(f.param_types) else None
        # A captured nonlocal is passed as a pointer to its shared box. Reads
        # and writes go through _name_value_ptr; the parameter slot itself
        # must therefore retain the box pointer rather than pretending it is
        # the annotated scalar value.
        ty = (
            PTR
            if pname in declared_nonlocals
            else ir_type_for(annot[0]) if isinstance(annot, tuple) else I64
        )
        pv = IRValue(f"%arg_{pname}", ty)
        params.append(pv)
        ptr = ctx.ensure_slot(pname, ty)
        ctx.emit(IRInstr("store", None, [pv, ptr]))
        if isinstance(annot, tuple) and annot[0] in ("outparam", "inparam"):
            # Pointee kind ("int"/"float"/"int8") for this parameter's
            # element-size/store-width in Subscript reads/IndexAssign
            # writes -- see _inparam_elem_addr's elem_size and the
            # int8 zext/trunc handling at each call site. Reuses
            # slot_el_ty (otherwise only used for list-typed locals) since
            # the shape ("this name has a tracked element kind") is the
            # same idea.
            ctx.slot_el_ty[pname] = annot[1] or "int"
        elif isinstance(annot, tuple) and len(annot) >= 2 and annot[0] in (
            "list",
            "tuple",
        ) and annot[1]:
            # Element kind of a `list[T]`/`tuple[T]` PARAMETER. A list-typed
            # LOCAL has always recorded this (see the Assign handler's own
            # `ctx.slot_el_ty[...] = ...`), but a parameter never did, so every
            # open-coded buffer walk keyed off `ctx.slot_el_ty` -- most visibly
            # `for x in xs:` -- fell back to the "int" default and loaded each
            # element as a raw I64 regardless of what it actually holds.
            # Two things were wrong as a result: a `list[float]` parameter's
            # elements were read as ints (the double's bit pattern used as a
            # number), and a `list[any]` parameter's elements (an unannotated
            # `*args`, whose elements are BOXED) were read as ints too, leaving
            # the box POINTER in play instead of the value.
            ctx.slot_el_ty[pname] = annot[1]

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
    """Same search order as `_resolve_class_chain`, including extra bases, but
    driven by a bare signature table (no _FuncCtx available here)."""
    seen: set[str] = set()
    stack: list[str] = [class_name]
    while stack:
        cur = stack.pop(0)
        if cur is None or cur in seen:
            continue
        seen.add(cur)
        sig = classes_sig.get(cur)
        if sig is None:
            continue
        if method in sig.methods:
            return cur
        _nexts: list = []
        if sig.parent is not None:
            _nexts.append(sig.parent)
        for _eb in getattr(sig, "extra_bases", []) or []:
            _nexts.append(_eb)
        stack = _nexts + stack
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

    def add_resolved_virtual(class_name: str, method: str) -> None:
        # Walker-side counterpart to ir_lower.py's own `_virtual_dispatch_
        # rows` (used at LOWERING time to emit a runtime __class__-id
        # dispatch chain whenever more than one subclass overrides a
        # method) -- that function makes lowering correctly CALL every
        # subclass override's real symbol, but nothing previously marked
        # those overrides reachable for THIS walker, which only ever
        # looked upward from the receiver's own static type
        # (add_resolved/_resolve_method_owner_in_sigs). Any user class
        # that is `class_name` or descends from it and resolves `method`
        # anywhere on its own chain needs its owner marked reachable too
        # -- not just the statically-resolved owner -- since the
        # receiver's runtime __class__ may be any of them. Same
        # "lowering fixed, walker not fixed" shape as every other
        # dunder/lambda/super() dispatch gap fixed this session; mirrors
        # `_virtual_dispatch_rows`'s downward scan but reimplemented
        # against the module-level `classes_sig` dict (no live `ctx` /
        # `class_ids` exists yet at this walker's point in the pipeline).
        add_resolved(class_name, method)
        for cname in classes_sig:
            seen: set[str] = set()
            cur = cname
            descends = False
            while cur is not None and cur not in seen:
                seen.add(cur)
                if cur == class_name:
                    descends = True
                    break
                sig = classes_sig.get(cur)
                cur = sig.parent if sig is not None else None
            if descends:
                add_resolved(cname, method)

    def visit(node) -> None:
        if node is None or isinstance(node, (str, int, float, bool)):
            return
        if isinstance(node, A.MethodCall):
            obj_ty = A.expr_type(node.obj)
            # `overload` extension: a resolved overload method call's real
            # target is its mangled method name (node.method is the
            # original, ambiguous, never-actually-emitted bare name --
            # every source-level @overload method was renamed to its own
            # mangled name during sema's per-class overload pre-pass).
            resolved_ov_m = getattr(node, "resolved_overload_symbol", None)
            dispatch_method = resolved_ov_m if resolved_ov_m is not None else node.method
            if obj_ty.startswith("instance:"):
                add_resolved_virtual(obj_ty.split(":", 1)[1], dispatch_method)
            elif obj_ty == "any":
                # Method call on an opaque/`any`-typed receiver whose static
                # type names no class -- ir_lower.py's own MethodCall lowering
                # now emits a runtime __class__-id dispatch chain over EVERY
                # user class resolving this method (`_classes_resolving_method`)
                # instead of the old graceful-no-op stub. Mark each such
                # candidate owner reachable, mirroring that whole-program scan,
                # or the emitted `{owner}__{method}` calls would be undefined
                # symbols at link time. Same "lowering fixed, walker not fixed"
                # shape as every other dispatch gap this session; reimplemented
                # against module-level `classes_sig` (no live `class_ids` yet).
                for cname in classes_sig:
                    add_resolved(cname, dispatch_method)
            elif obj_ty.startswith("super:"):
                # super().method(...): dispatches statically to the base
                # class's OWN method (never a subclass override -- see
                # ir_lower.py's `super:` MethodCall lowering), so mark
                # exactly that resolved owner reachable. Same
                # "lowering fixed, walker not fixed" shape as every other
                # dunder/lambda dispatch gap this session -- add_resolved
                # already handles the None-owner (non-user-class base)
                # case as a no-op.
                add_resolved(obj_ty.split(":", 1)[1], node.method)
            elif obj_ty == "type" and isinstance(node.obj, A.Name) and node.obj.name in class_names:
                add_resolved(node.obj.name, node.method)
            elif obj_ty == "type":
                # A classmethod/staticmethod call on a `type` value whose
                # concrete class isn't a literal name (a variable holding a
                # class, e.g. iterating `(Server, Shared, Client)`). ir_lower's
                # MethodCall lowering emits a runtime class-id dispatch chain
                # over EVERY user class resolving this method (mirroring the
                # `any` case above, but keyed off the type value itself rather
                # than an instance's __class__ tag). Mark every such candidate
                # owner reachable, or the emitted `{owner}__{method}` calls
                # would be undefined symbols at link time.
                for cname in classes_sig:
                    add_resolved(cname, dispatch_method)
            # `node.obj` (the receiver) is visited (or deliberately
            # skipped, for a bare Name) once, uniformly for both
            # MethodCall and Attr, in the generic dataclass-field
            # recursion below -- see its own comment for why.
            sort_key = getattr(node, "sort_key", None)
            if isinstance(sort_key, A.Lambda):
                # list.sort(key=lambda ...) -- same gap as sorted()/min()/
                # max()'s A.Call case just below; MethodCall needs its own
                # check since list.sort() isn't an A.Call at all.
                add_func(getattr(sort_key, "func_name", None))
        elif isinstance(node, A.Name):
            add_func(node.name)
        elif isinstance(node, A.Call):
            # `overload` extension: a resolved overload call's real target
            # is its mangled symbol (node.func is the original, ambiguous,
            # never-actually-emitted bare name -- every source-level
            # @overload def was renamed to its own mangled symbol during
            # sema's pre-pass, so add_func(node.func) alone would never
            # mark the real, called-into symbol reachable at all).
            resolved_ov = getattr(node, "resolved_overload_symbol", None)
            if resolved_ov is not None:
                add_func(resolved_ov)
            else:
                add_func(node.func)
            if node.func in class_names:
                add_resolved(node.func, "__init__")
            sort_key = getattr(node, "sort_key", None)
            if isinstance(sort_key, A.Lambda):
                # sorted(..., key=lambda w: ...)/min()/max() with a key
                # lambda whose body isn't the identity/tuple-index fast
                # path (see _lower_sort_inplace) CALLS the lambda's own
                # synthesized function at lowering time, but nothing
                # marked that function reachable for the walker -- same
                # two-part "lowering fixed, walker not fixed" shape as
                # every other dunder/lambda dispatch fix this session.
                add_func(getattr(sort_key, "func_name", None))
            owner = getattr(node, "dunder_call_owner", None)
            if owner is not None:
                add(owner, "__call__")
            if node.func == "len" and len(node.args) == 1:
                arg_t = A.expr_type(node.args[0])
                if arg_t.startswith("instance:"):
                    add_resolved(arg_t.split(":", 1)[1], "__len__")
            if node.func == "bool" and len(node.args) == 1:
                arg_t = A.expr_type(node.args[0])
                if arg_t.startswith("instance:"):
                    cls_name = arg_t.split(":", 1)[1]
                    add_resolved(cls_name, "__bool__")
                    add_resolved(cls_name, "__len__")
            if node.func == "abs" and len(node.args) == 1:
                arg_t = A.expr_type(node.args[0])
                if arg_t.startswith("instance:"):
                    add_resolved(arg_t.split(":", 1)[1], "__abs__")
            if node.func == "hash" and len(node.args) == 1:
                arg_t = A.expr_type(node.args[0])
                if arg_t.startswith("instance:"):
                    add_resolved(arg_t.split(":", 1)[1], "__hash__")
            if node.func in ("next", "iter") and len(node.args) >= 1:
                # `next(g)` / `iter(g)` called DIRECTLY on an iterator object --
                # including a generator function's result, which sema desugars
                # into exactly such a class. The `for` and comprehension arms
                # cover iteration syntax; this covers the builtin, which
                # otherwise had nothing keeping `__next__` alive:
                # "undefined symbol _genobj_counter____next__" at link time.
                _nt = A.expr_type(node.args[0])
                if _nt.startswith("instance:"):
                    add_resolved(_nt.split(":", 1)[1], "__next__")
                    add_resolved(_nt.split(":", 1)[1], "__iter__")
            if node.func in ("int", "float") and len(node.args) == 1:
                arg_t = A.expr_type(node.args[0])
                if arg_t.startswith("instance:"):
                    # Both dunders: `int()` falls back to `__float__` and
                    # `float()` dispatches to it, so either can be the one this
                    # call reaches. Losing it to DCE fails the link.
                    add_resolved(arg_t.split(":", 1)[1], "__int__")
                    add_resolved(arg_t.split(":", 1)[1], "__float__")
            if node.func in ("list", "tuple") and len(node.args) == 1:
                # `list(obj)` drains an iterable object through either the
                # iterator protocol or the sequence protocol (see the matching
                # lowering) -- keep whichever dunders that dispatch needs, or
                # DCE drops them and the link fails on e.g.
                # `_genobj_countdown____iter__`.
                arg_t = A.expr_type(node.args[0])
                if arg_t.startswith("instance:"):
                    _lcls = arg_t.split(":", 1)[1]
                    for _dn in ("__iter__", "__next__", "__len__", "__getitem__"):
                        if _resolve_method_owner_in_sigs(classes_sig, _lcls, _dn) is not None:
                            add_resolved(_lcls, _dn)
            if node.func in ("print", "str", "repr"):
                # `print(instance)` / `str(instance)` / `repr(instance)`
                # -- the value-to-string coercion these all funnel
                # through (`_lower_expr_as_str`) dispatches to
                # `__str__`/`__repr__` at LOWERING time, but nothing
                # marked that dispatch reachable for the WALKER -- only
                # an f-string SEGMENT's instance-typed value had this
                # check (see the `A.FString` case below). A plain
                # `print(a)` on a `Fraction`-style instance therefore
                # correctly CALLED `__str__` but the method was never
                # EMITTED into the binary at all -- the same two-part
                # "lowering fixed, walker not fixed" shape as every
                # other dunder-dispatch bug this session. Mirrors the
                # f-string case's own __str__-then-__repr__ fallback
                # order exactly.
                for arg in node.args:
                    arg_t = A.expr_type(arg)
                    if arg_t.startswith("instance:"):
                        cls_name = arg_t.split(":", 1)[1]
                        # `repr(x)` prefers __repr__ first (matches
                        # `_lower_expr_as_str`'s own `repr_mode`/`repr_first`
                        # order) -- print()/str() prefer __str__ first. A
                        # class defining BOTH (e.g. UUID) previously always
                        # got __str__ marked reachable here regardless of
                        # which one the call site actually dispatches to at
                        # lowering time, so `repr(u)` on a class with both
                        # dunders correctly CALLED __repr__ but never
                        # emitted it -- undefined symbol at link time.
                        first, second = (
                            ("__repr__", "__str__") if node.func == "repr" else ("__str__", "__repr__")
                        )
                        owner = (
                            _resolve_method_owner_in_sigs(classes_sig, cls_name, first)
                            or _resolve_method_owner_in_sigs(classes_sig, cls_name, second)
                        )
                        method = first if _resolve_method_owner_in_sigs(classes_sig, cls_name, first) is not None else second
                        add(owner, method)
                    elif arg_t == "list":
                        # `print([inst, ...])` reprs each element via the class's
                        # __repr__ (containers use repr for elements -- see
                        # ir_lower's `_lower_list_instance_repr`). Same
                        # "lowering dispatches, walker must keep it live" shape;
                        # match that helper's repr-first resolution.
                        # A LITERAL list carries its element kind in `el_type`,
                        # not `list_el_type` -- exactly the split the repr
                        # dispatch itself honours. Reading only the latter left
                        # `print([Point(1, 2)])` with no dunder marked, so DCE
                        # dropped the `__repr__` the lowering then called:
                        # "undefined symbol Point____repr__" at link time.
                        _lel = (
                            (getattr(arg, "el_type", "") or "")
                            if isinstance(arg, A.ListLit)
                            else (getattr(arg, "list_el_type", "") or "")
                        )
                        if _lel.startswith("instance:"):
                            _lcn = _lel.split(":", 1)[1]
                            _lowner = (
                                _resolve_method_owner_in_sigs(classes_sig, _lcn, "__repr__")
                                or _resolve_method_owner_in_sigs(classes_sig, _lcn, "__str__")
                            )
                            if _lowner is not None:
                                _lmethod = (
                                    "__repr__"
                                    if _resolve_method_owner_in_sigs(classes_sig, _lcn, "__repr__") is not None
                                    else "__str__"
                                )
                                add(_lowner, _lmethod)
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
            setitem_owner = getattr(node, "_setitem_class", None)
            if setitem_owner is not None:
                # `dd[key] = value` on an instance (sema stamps
                # `_setitem_class` on the IndexAssign's `target` Subscript,
                # mirroring `_getitem_class` on a read-side Subscript) --
                # same "lowering dispatches to __setitem__, walker didn't
                # know" gap as every other dunder fixed this session.
                add_resolved(setitem_owner, "__setitem__")
        elif isinstance(node, (A.If, A.While)) and A.expr_type(node.test).startswith("instance:"):
            # `if obj:` / `while obj:` on a user instance dispatches to
            # `__bool__`/`__len__` (see `_lower_truthy`'s own new dunder
            # check) -- that dispatch decision isn't stamped anywhere on
            # the AST node itself (unlike BinOp/UnaryOp/Compare's
            # dunder_owner), so this walker never knew to keep the
            # method reachable. Without this, `_lower_truthy` correctly
            # LOWERED the call, but the method it called was never
            # EMITTED into the final binary at all -- an unresolved
            # symbol at link time (confirmed via
            # `369_dunder_bool.py`: `undefined symbol 'Box____bool__'`),
            # the exact same two-part "lowering fixed, walker not fixed"
            # bug shape as the earlier unary-dunder fix this session.
            cls_name = A.expr_type(node.test).split(":", 1)[1]
            for mname in ("__bool__", "__len__"):
                add_resolved(cls_name, mname)
        elif isinstance(node, A.For) and getattr(node, "iter_is_instance", None) is not None:
            # `for x in obj:` on a user class with __iter__/__next__ --
            # same two-part "lowering fixed, walker not fixed" shape as
            # every other dunder-dispatch fix this session. sema.py
            # already validated both methods exist when it stamped
            # `iter_is_instance` (see its own A.For handling), so no
            # inheritance-chain resolution is needed here beyond what
            # `add_resolved` already does.
            cls_name = node.iter_is_instance
            add_resolved(cls_name, "__iter__")
            add_resolved(cls_name, "__next__")
        elif isinstance(node, A.Comprehension) and A.expr_type(node.iter).startswith("instance:"):
            # `[elt for x in obj]` on a user class with __iter__/__next__
            # (also covers a yield-based generator function's returned
            # object -- see `_lower_comprehension_instance_iter`'s own
            # docstring for why that's the same shape) -- same two-part
            # "lowering fixed, walker not fixed" gap as A.For's identical
            # `iter_is_instance` case just above. Unlike A.For, sema.py
            # never stamps a dedicated marker attribute on A.Comprehension
            # for this shape (only A.For gets `iter_is_instance`), so this
            # re-derives the class name the same way the lowering itself
            # does: straight off `A.expr_type(node.iter)`.
            cls_name = A.expr_type(node.iter).split(":", 1)[1]
            add_resolved(cls_name, "__iter__")
            add_resolved(cls_name, "__next__")
        elif isinstance(node, A.AugAssign) and A.expr_type(node.value).startswith("instance:"):
            # `v1 += v2` on a user instance dispatches to
            # `__iadd__`/`__add__` (see `_lower_stmt`'s AugAssign case,
            # `_AUGASSIGN_DUNDER`) -- same two-part shape as every other
            # dunder-dispatch fix this session: the lowering resolves
            # and CALLS the right method, but nothing told this walker
            # to keep it reachable, so it was correctly called but never
            # emitted (undefined symbol at link time). Mirrors the
            # lowering's own class-name derivation: reads it off the
            # RHS's resolved type, since `s.target`/`node.target` here
            # is a bare string with no cached type of its own.
            cls_name = A.expr_type(node.value).split(":", 1)[1]
            inplace_dunder, dunder = _AUGASSIGN_DUNDER.get(node.op, (None, None))
            if inplace_dunder is not None:
                add_resolved(cls_name, inplace_dunder)
                add_resolved(cls_name, dunder)
        elif isinstance(node, A.FString):
            for seg in node.segments:
                seg_ty = A.expr_type(seg)
                if seg_ty.startswith("instance:"):
                    cls_name = seg_ty.split(":", 1)[1]
                    # BOTH dunders, when the class has both: a segment's `!r`
                    # conversion dispatches to `__repr__` while a plain one
                    # takes `__str__`, and the conversion is per-segment. Marking
                    # only the str-first winner left `f'{P()!r}'` calling a
                    # `__repr__` that DCE had already dropped -- undefined symbol
                    # at link time. Keeping an extra method costs a little code
                    # size; dropping a called one does not link.
                    for _m in ("__str__", "__repr__"):
                        _o = _resolve_method_owner_in_sigs(classes_sig, cls_name, _m)
                        if _o is not None:
                            add(_o, _m)

        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, tuple):
            for item in node:
                visit(item)
            return
        if is_dataclass(node):
            # `node.obj` on a MethodCall is the RECEIVER expression, not a
            # free-standing name reference -- the MethodCall branch above
            # already interprets it fully via A.expr_type(node.obj)
            # (instance:/super:/type dispatch). Re-visiting it here as an
            # ordinary field would hit the generic `elif isinstance(node,
            # A.Name): add_func(node.name)` case on its own separate top-
            # level visit() call, spuriously marking any MODULE-LEVEL
            # FUNCTION that happens to share the receiver variable's bare
            # name as reachable (e.g. `log = logging.getLogger(...);
            # log.error(...)` colliding with logging.py's own unrelated
            # top-level `def log(...)` function) -- confirmed as a real
            # bug via a background investigation: the walker doesn't
            # track variable/scope bindings at all, so it can't otherwise
            # tell "this Name is a receiver expression" from "this Name
            # is a genuine function-value reference" by field position
            # alone once both branches see the same bare Name node twice.
            # Skipping `obj` here is safe -- MethodCall's own branch
            # already fully handles it; nothing else needs a second visit.
            # A.Attr (`log.name`, plain attribute access, no call at all)
            # has the identical exposure with no dedicated branch above
            # to have "already handled" it -- confirmed as a second real
            # instance of the same collision (236_logging_module.py's
            # `log = logging.getLogger(...); print(log.name)`, caught
            # only after pe_linker.py's new duplicate-symbol check turned
            # the prior silent corruption into a loud, correct error).
            # For both node types, only SKIP a bare-Name `.obj` (nothing
            # further to discover there); a non-Name receiver (a chained
            # call/attr expression) still needs its own visit for any
            # reachable calls nested inside it.
            if isinstance(node, (A.MethodCall, A.Attr)):
                if not isinstance(node.obj, A.Name):
                    visit(node.obj)
                skip_field = "obj"
            else:
                skip_field = None
            for f in fields(node):
                if f.name == "pos" or f.name == skip_field:
                    continue
                visit(getattr(node, f.name))

    for st in mod.body:
        visit(st)
    # Class-var DEFAULT initializers (`name = Property(...)` in a class body)
    # run at module startup for EVERY class (lower_module emits them all as
    # __cv_<Class>__<var> init statements), so any constructor/function they
    # reference is genuinely reachable and its body must be emitted -- e.g. a
    # `Property` class built only by other classes' field defaults, whose
    # `Property____init__` was otherwise an undefined symbol at link time.
    for cls in mod.classes:
        for cv in getattr(cls, "class_vars", []) or []:
            if cv[2] is not None:
                visit(cv[2])
    if any(f.name == "main" for f in mod.funcs):
        add_func("main")
    if any(f.name == "_threading_bootstrap" for f in mod.funcs):
        add_func("_threading_bootstrap")
    # Native-library exports (`@access(Public)` / `@abi(...)`) are called
    # only from OUTSIDE the compiled program (a host language dlopen/
    # GetProcAddress-ing the symbol) -- nothing in `mod.body`/`main` ever
    # calls them, so ordinary reachability from the process entry point
    # would never mark them needed and lowering would silently drop them
    # before pe_linker.py/elf_linker.py even get a chance to export them.
    # Treat every exported function/method as its own root, exactly like
    # `main` above.
    for f in mod.funcs:
        if f.is_public_export:
            add_func(f.name)
    for cls in mod.classes:
        class_public = cls.is_public_export
        for m in cls.methods:
            if class_public or m.is_public_export:
                add(cls.name, m.name)

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
                    access_policy=m.access_policy,
                    abi_name=m.abi_name,
                    is_public_export=cls.is_public_export or m.is_public_export,
                    decorators=list(m.decorators),
                    method_owner_class=cls.name,
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
    # import_binary()/.imported: scan the FULL (not reachability-filtered)
    # top-level function list for `@<handle>.imported` stubs -- mirrors
    # codegen.py's identical scan in Codegen.__init__. These stubs are
    # never called as ordinary functions (only ever as `handle.func(...)`,
    # a MethodCall dispatched specially below), so they legitimately don't
    # show up in `top_funcs`/`_reachable_callables`'s reachable set; this
    # dict is the only place their FuncDef (needed for its param_types,
    # to marshal each call's arguments) is still reachable from lowering.
    imported_funcs: dict[str, list[tuple[str, "A.FuncDef"]]] = {}
    for f in mod.funcs:
        for deco in f.decorators:
            if deco.endswith(".imported"):
                handle_name = deco[: -len(".imported")]
                imported_funcs.setdefault(handle_name, []).append((f.name, f))
    mctx = _ModuleCtx(
        frozenset(c.name for c in mod.classes),
        frozenset(f.name for f in top_funcs) | frozenset(f.name for f in method_funcs),
        func_sigs,
        mod.ffi_funcs,
        getattr(mod, "ffi_consts", {}),
        getattr(mod, "imported_modules", {}),
        getattr(mod, "classes_sig", {}),
        global_types,
        global_list_el_ty,
        mod.classes,
        getattr(mod, "mlang_code_funcs", {}),
        imported_funcs,
    )
    # Raw per-parameter annotation tuples per callee, keyed by the symbol its
    # call sites emit (a plain function's name; `{Class}__{method}` /
    # `{Class}____init__` for methods/constructors). Preserves the full
    # annotation (unlike FuncSig.param_types, collapsed to base-strings) so a
    # call site can route each argument through the store choke point with
    # the PARAMETER's slot type. A method's parameter 0 is `self`.
    mctx.func_param_annots = {
        f.name: list(getattr(f, "param_types", []) or []) for f in mod.funcs
    }
    for cls in mod.classes:
        for m in cls.methods:
            mctx.func_param_annots[f"{cls.name}__{m.name}"] = list(
                getattr(m, "param_types", []) or []
            )
    mctx.lifted_free_vars = {
        f.name: list(f.free_vars)
        for f in mod.funcs
        if getattr(f, "is_lifted", False) and f.free_vars
    }
    mctx.lifted_nonlocal_vars = {
        f.name: set(f.nonlocal_vars)
        for f in mod.funcs
        if getattr(f, "is_lifted", False) and f.nonlocal_vars
    }
    # Register each class-var global's type into `global_types` (and
    # therefore `global_names`, computed from it in `_ModuleCtx.__init__`)
    # too -- not just `mctx.data` -- so `_is_global_name`/`_name_ptr`
    # correctly resolve the synthesized init-statement writes below (and
    # any `ClassName.attr` read/write elsewhere) as real global accesses
    # rather than falling back to a same-named local slot.
    for label, default_expr in mctx.class_var_defaults:
        global_types[label] = ir_type_for(A.expr_type(default_expr))
    for label in mctx.class_object_labels.values():
        global_types[label] = PTR
    mctx.global_names = frozenset(global_types)
    for name in sorted(global_types):
        mctx.data.append(IRGlobal(name=name, type=global_types[name], value=None))
    exports = [f.name for f in top_funcs if f.is_public_export]
    exports.extend(f.name for f in method_funcs if f.is_public_export)
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
    if has_explicit_main or mod.force_module_init:
        init_body = class_var_init_stmts + _module_init_stmts(mod)
        # Emit __asmpy_module_init whenever there are user classes even if
        # init_body is empty: lower_func(module_body=True) is what allocates
        # each class's mutable namespace dict (`__classobj_<name>`, the
        # _abi_new_instance loop at entry), and a `cls.attr = v` / literal
        # `ClassName.attr = v` store reads that global. With no class vars and
        # no guarded module statements the function was skipped, leaving every
        # __classobj_<name> a null pointer -- _abi_dict_set on it faulted.
        if init_body or mctx.class_object_labels:
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
    return IRModule(funcs=funcs, data=mctx.data, exports=exports)


# ── import_binary() dynamic DLL loading ─────────────────────────────────────
#
# `handle = import_binary(path)` loads a DLL/shared object at RUNTIME via
# LoadLibraryA (this backend currently targets Windows/PE only -- see
# pe_linker.py's _DLL_FOR_SYMBOL, which already imports LoadLibraryA/
# GetProcAddress from kernel32.dll for other purposes, so no new linker
# wiring is needed), then eagerly resolves every function the program
# declared with `@<handle>.imported` against it via GetProcAddress,
# mirroring codegen.py's `_gen_import_binary`/`_gen_dynamic_call` exactly
# (same eager-resolve-at-load-time strategy, same "handle is a real
# instance dict, resolved pointers stored under each function's own name"
# representation -- see codegen.py for the full rationale, including why
# this is eager instead of gl_import()'s lazy resolution: import_binary()
# has no equivalent to gl_import()'s "needs a GL context first" ordering
# hazard, so there's no reason to defer).
#
# The call itself (`handle.func(args)`) is an INDIRECT call through the
# resolved pointer -- no new IR mechanism was needed for this: the x86-64
# backend's "call" IRInstr op already supports an indirect call whenever
# its first operand is an IRValue (a runtime pointer) instead of a bare
# string symbol name (see _backends/x86_64/codegen.py's `_call`, `is_indirect
# = hasattr(target_op, "name") and hasattr(target_op, "type")`) -- this is
# already exercised by ordinary lambda/closure calls elsewhere in this
# pipeline, just reused here.
#
# Not `_reachable_callables`-aware: a `@handle.imported` stub is never
# dispatched to as a user-defined function/method (there is no
# `DynamicModule__toupper` symbol anywhere) -- it's purely a decorator
# carrying the function's name/signature for this dict-driven runtime
# resolution, so the walker's job of deciding which USER functions/methods
# stay reachable doesn't apply. The stub's FuncDef is read directly out of
# `mctx.imported_funcs` (built in lower_module from the whole, unfiltered
# `mod.funcs`), and its trivial `pass` body is simply never lowered/
# compiled -- exactly like codegen.py, which never emits these stubs as
# real functions either.

_DYNCALL_HANDLE_KEY = "_handle"


def _lower_import_binary_assign(ctx: _FuncCtx, s: A.Assign) -> None:
    """`handle = import_binary(path)`: alloc a fresh instance dict, load the
    named library into it (keyed "_handle", unused today but keeps the
    raw HMODULE available for a future close_binary()), then resolve every
    `@<handle>.imported` function registered for this exact target name
    (mctx.imported_funcs) via GetProcAddress, storing each resolved
    pointer keyed by the function's own name -- read back by
    _lower_dynamic_call at each `handle.func(...)` call site.
    """
    e = s.value  # the import_binary(path) Call
    dict_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", dict_v, ["_abi_new_instance"]))

    path_v = _lower_expr(ctx, e.args[0])
    lib_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", lib_v, ["LoadLibraryA", path_v]))

    handle_key_name = ctx.mctx.intern_str(_DYNCALL_HANDLE_KEY)
    handle_key_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", handle_key_v, [handle_key_name]))
    ctx.emit(IRInstr("call", None, ["_abi_dict_set", dict_v, handle_key_v, lib_v]))

    for func_name, _funcdef in ctx.mctx.imported_funcs.get(s.target, []):
        name_label = ctx.mctx.intern_str(func_name)
        name_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", name_v, [name_label]))
        proc_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", proc_v, ["GetProcAddress", lib_v, name_v]))
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", dict_v, name_v, proc_v]))

    ptr = _name_ptr(ctx, s.target, PTR)
    ctx.emit(IRInstr("store", None, [dict_v, ptr]))


def _lower_dynamic_call(ctx: _FuncCtx, e: A.MethodCall) -> IRValue:
    """`handle.func(args)` for a `@handle.imported` function: look up the
    pointer GetProcAddress already resolved into the handle dict (keyed by
    `func`'s name, see _lower_import_binary_assign) and call through it
    indirectly. Scalar int/float/str parameters are supported -- an
    unannotated parameter defaults to "int", matching codegen.py's
    _gen_dynamic_call (the foreign function's real signature isn't
    introspectable, so the stub's own annotations are the only contract).
    """
    handle_name = e.obj.name if isinstance(e.obj, A.Name) else None
    funcdef = None
    if handle_name is not None:
        for fname, fdef in ctx.mctx.imported_funcs.get(handle_name, []):
            if fname == e.method:
                funcdef = fdef
                break
    if funcdef is None:
        raise LowerError(
            f"unsupported expr MethodCall ({e.method!r} is not a "
            "@<handle>.imported function on a known import_binary() handle)"
        )

    args: list[IRValue] = []
    for i, a in enumerate(e.args):
        annot = funcdef.param_types[i] if i < len(funcdef.param_types) else None
        base = annot[0] if annot else "int"
        av = _lower_expr(ctx, a)
        if base == "float" and av.type is not F64:
            fv = ctx.tmp(F64)
            ctx.emit(IRInstr("sitofp", fv, [av]))
            av = fv
        elif base != "float" and av.type is F64:
            raise LowerError(
                f"@{handle_name}.imported function {funcdef.name!r}: "
                "passing a float argument to a non-float parameter is not supported"
            )
        elif base not in ("int", "float", "str"):
            raise LowerError(
                f"@{handle_name}.imported function {funcdef.name!r}: "
                f"parameter type {base!r} is not supported "
                "(only int/float/str)"
            )
        args.append(av)

    obj_v = _lower_expr(ctx, e.obj)
    name_label = ctx.mctx.intern_str(e.method)
    name_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("global_addr", name_v, [name_label]))
    zero_default = ctx.tmp(PTR)
    ctx.emit(IRInstr("const", zero_default, [0]))
    ptr_v = ctx.tmp(PTR)
    ctx.emit(IRInstr("call", ptr_v, ["_abi_dict_get_default", obj_v, name_v, zero_default]))

    ret_base = funcdef.ret_type[0] if funcdef.ret_type else "int"
    res_ty = F64 if ret_base == "float" else I64
    v = ctx.tmp(res_ty)
    ctx.emit(IRInstr("call", v, [ptr_v, *args]))
    return v
