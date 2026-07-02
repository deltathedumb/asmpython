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


class LowerError(Exception):
    pass


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
        ffi_funcs: dict | None = None,
    ) -> None:
        self.data: list[IRGlobal] = []
        self.class_names = class_names
        self.ffi_funcs = ffi_funcs or {}
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
    def __init__(self, mctx: _ModuleCtx) -> None:
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
        self.loop_stack: list[tuple[str, str]] = []  # (continue_label, break_label)
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


_BINOP = {
    "+": "iadd", "-": "isub", "*": "imul",
    "//": "idiv", "%": "irem",
    "&": "iand", "|": "ior", "^": "ixor",
    "<<": "shl", ">>": "shr",
}

_CMPOP = {
    "==": "icmp.eq", "!=": "icmp.ne",
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
        ty = ctx.slot_ty.get(e.name, I64)
        ptr = ctx.ensure_slot(e.name, ty)
        v = ctx.tmp(ty)
        ctx.emit(IRInstr("load", v, [ptr]))
        return v

    if isinstance(e, A.UnaryOp):
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
        if lt == "float" or rt == "float":
            if e.op not in _FBINOP:
                raise LowerError(f"unsupported float binop {e.op!r}")
            a = _lower_expr(ctx, e.left)
            b = _lower_expr(ctx, e.right)
            v = ctx.tmp(F64)
            ctx.emit(IRInstr(_FBINOP[e.op], v, [a, b]))
            return v
        if e.op not in _BINOP:
            raise LowerError(f"unsupported binop {e.op!r}")
        a = _lower_expr(ctx, e.left)
        b = _lower_expr(ctx, e.right)
        v = ctx.tmp(I64)
        ctx.emit(IRInstr(_BINOP[e.op], v, [a, b]))
        return v

    if isinstance(e, A.Compare):
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

    if isinstance(e, A.Call) and e.func == "len" and len(e.args) == 1 and A.expr_type(e.args[0]) in ("list", "tuple"):
        list_v = _lower_expr(ctx, e.args[0])
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [list_v, _LIST_LEN_OFF]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", v, [len_addr]))
        return v

    if isinstance(e, A.Call) and e.func == "print":
        # print(x) -> printf(fmt, x); newline baked into the format string
        # since asmpython's print() always appends one. Only int/str args
        # for now -- float/list/dict printing needs the runtime helpers
        # (_emit_float_to_str etc.), not yet wired into this pipeline.
        if not e.args:
            fmt_name = ctx.mctx.intern_str("\n")
            fmt_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", fmt_ptr, [fmt_name]))
            ctx.emit(IRInstr("call", None, ["printf", fmt_ptr]))
        else:
            arg = e.args[0]
            arg_ty = A.expr_type(arg)
            val = _lower_expr(ctx, arg)
            if arg_ty == "str":
                fmt_str = "%s\n"
            elif arg_ty == "float":
                fmt_str = "%g\n"
            else:
                fmt_str = "%lld\n"
            fmt_name = ctx.mctx.intern_str(fmt_str)
            fmt_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", fmt_ptr, [fmt_name]))
            ctx.emit(IRInstr("call", None, ["printf", fmt_ptr, val]))
        return ctx.shared_zero

    if isinstance(e, A.ListLit):
        n = len(e.elems)
        cap_v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", cap_v, [max(n, 1)]))
        list_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", list_v, ["_abi_new_list", cap_v]))
        for el in e.elems:
            if A.expr_type(el) == "float":
                raise LowerError("unsupported expr ListLit (float elements)")
            val = _lower_expr(ctx, el)
            ctx.emit(IRInstr("call", None, ["_abi_list_append", list_v, val]))
        return list_v

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
            if k is None:
                raise LowerError("unsupported expr DictLit (** spread)")
            if not isinstance(k, A.StrLit):
                raise LowerError("unsupported expr DictLit (non-literal key)")
            if A.expr_type(v) == "float":
                raise LowerError("unsupported expr DictLit (float value)")
            key_name = ctx.mctx.intern_str(k.value)
            key_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", key_ptr, [key_name]))
            val = _lower_expr(ctx, v)
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", dict_v, key_ptr, val]))
        return dict_v

    if isinstance(e, A.Subscript):
        if isinstance(e.index, A.Slice):
            raise LowerError("unsupported expr Subscript (slice)")
        obj_ty = A.expr_type(e.obj)
        if obj_ty == "dict":
            if A.expr_type(e.index) != "str":
                raise LowerError("unsupported expr Subscript (non-str dict key)")
            if A.expr_type(e) == "float":
                raise LowerError("unsupported expr Subscript (float dict value)")
            obj_v = _lower_expr(ctx, e.obj)
            key_v = _lower_expr(ctx, e.index)
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            v = ctx.tmp(I64)
            ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_v, key_v, zero]))
            return v
        if obj_ty not in ("list", "tuple"):
            raise LowerError(f"unsupported expr Subscript ({obj_ty})")
        result_ty = A.expr_type(e)
        if obj_ty == "list" and result_ty == "float":
            raise LowerError("unsupported expr Subscript (float element)")
        obj_v = _lower_expr(ctx, e.obj)
        idx_v = _lower_expr(ctx, e.index)
        addr = _list_elem_addr(ctx, obj_v, idx_v)
        v = ctx.tmp(F64 if result_ty == "float" else I64)
        ctx.emit(IRInstr("load", v, [addr]))
        return v

    if isinstance(e, A.MethodCall):
        obj_ty = A.expr_type(e.obj)
        if obj_ty == "list":
            if e.method == "append" and len(e.args) == 1:
                if A.expr_type(e.args[0]) == "float":
                    raise LowerError("unsupported expr MethodCall (list.append float element)")
                obj_v = _lower_expr(ctx, e.obj)
                val = _lower_expr(ctx, e.args[0])
                ctx.emit(IRInstr("call", None, ["_abi_list_append", obj_v, val]))
                return ctx.shared_zero  # list.append() returns None
            if e.method == "pop" and not e.args:
                if A.expr_type(e) == "float":
                    raise LowerError("unsupported expr MethodCall (list.pop float element)")
                obj_v = _lower_expr(ctx, e.obj)
                v = ctx.tmp(ir_type_for(A.expr_type(e)))
                ctx.emit(IRInstr("call", v, ["_abi_list_pop", obj_v]))
                return v
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
            raise LowerError(f"unsupported expr MethodCall (list.{e.method})")
        if obj_ty == "dict":
            if e.method == "get" and len(e.args) in (1, 2):
                if A.expr_type(e.args[0]) != "str":
                    raise LowerError("unsupported expr MethodCall (dict.get non-str key)")
                obj_v = _lower_expr(ctx, e.obj)
                key_v = _lower_expr(ctx, e.args[0])
                if len(e.args) == 2:
                    if A.expr_type(e.args[1]) == "float":
                        raise LowerError("unsupported expr MethodCall (dict.get float default)")
                    default_v = _lower_expr(ctx, e.args[1])
                else:
                    default_v = ctx.tmp(I64)
                    ctx.emit(IRInstr("const", default_v, [0]))
                v = ctx.tmp(I64)
                ctx.emit(IRInstr("call", v, ["_abi_dict_get_default", obj_v, key_v, default_v]))
                return v
            raise LowerError(f"unsupported expr MethodCall (dict.{e.method})")
        if obj_ty == "str":
            obj_v = _lower_expr(ctx, e.obj)
            no_arg_str_methods = {
                "upper": "_abi_str_upper", "lower": "_abi_str_lower",
                "strip": "_abi_str_strip", "capitalize": "_abi_str_capitalize",
                "lstrip": "_abi_str_lstrip", "rstrip": "_abi_str_rstrip",
                "swapcase": "_abi_str_swapcase", "title": "_abi_str_title",
                "splitlines": "_abi_str_splitlines",
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
            if e.method == "split" and not e.args:
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_split_ws", obj_v]))
                return v
            if e.method == "split" and len(e.args) == 1:
                if A.expr_type(e.args[0]) != "str":
                    raise LowerError("unsupported expr MethodCall (str.split non-str sep)")
                arg_v = _lower_expr(ctx, e.args[0])
                v = ctx.tmp(PTR)
                ctx.emit(IRInstr("call", v, ["_abi_str_split", obj_v, arg_v]))
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
        raise LowerError(f"unsupported expr MethodCall ({obj_ty}.{e.method})")

    if isinstance(e, A.Attr):
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
        return v

    if isinstance(e, A.Call) and e.func in ctx.mctx.class_names:
        # ClassName(...) -> a fresh empty instance dict (no __init__ call
        # yet -- constructor wiring is a later step; for now this only
        # supports classes whose fields get set via plain attribute
        # assignment after construction).
        v = ctx.tmp(PTR)
        ctx.emit(IRInstr("call", v, ["_abi_new_instance"]))
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
        ctx.emit(IRInstr("call", v, [c_name, *args]))
        return v

    if isinstance(e, A.Call):
        args = [_lower_expr(ctx, a) for a in e.args]
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("call", v, [e.func, *args]))
        return v

    raise LowerError(f"unsupported expr {type(e).__name__}")


def _lower_stmt(ctx: _FuncCtx, s: A.Stmt) -> None:
    if isinstance(s, A.Pass):
        return

    if isinstance(s, A.Assign):
        val = _lower_expr(ctx, s.value)
        ptr = ctx.ensure_slot(s.target, val.type)
        ctx.emit(IRInstr("store", None, [val, ptr]))
        if isinstance(s.value, A.ListLit):
            ctx.slot_el_ty[s.target] = s.value.el_type
        return

    if isinstance(s, A.AugAssign):
        # `target op= value` -> `target = target op value`, same int/float
        # binop dispatch as a plain BinOp (the target's current static type
        # comes from its existing slot, defaulting to int for a first write
        # -- ensure_slot below mirrors A.Assign's own untyped-slot default).
        cur_ty = ctx.slot_ty.get(s.target, I64)
        ptr = ctx.ensure_slot(s.target, cur_ty)
        cur = ctx.tmp(cur_ty)
        ctx.emit(IRInstr("load", cur, [ptr]))
        rhs_ty = A.expr_type(s.value)
        rhs = _lower_expr(ctx, s.value)
        if cur_ty is F64 or rhs_ty == "float":
            if s.op not in _FBINOP:
                raise LowerError(f"unsupported float augassign op {s.op!r}")
            res = ctx.tmp(F64)
            ctx.emit(IRInstr(_FBINOP[s.op], res, [cur, rhs]))
            if cur_ty is not F64:
                # First write was int but this op promotes to float -- widen
                # the slot itself going forward isn't supported (slots are
                # fixed-type); reject rather than silently truncate back.
                raise LowerError("augassign int->float promotion needs a float-declared target")
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
            raise LowerError("unsupported stmt IndexAssign (slice)")
        obj_ty = A.expr_type(target.obj)
        if obj_ty == "dict":
            if A.expr_type(target.index) != "str":
                raise LowerError("unsupported stmt IndexAssign (non-str dict key)")
            if A.expr_type(s.value) == "float":
                raise LowerError("unsupported stmt IndexAssign (float dict value)")
            obj_v = _lower_expr(ctx, target.obj)
            key_v = _lower_expr(ctx, target.index)
            val = _lower_expr(ctx, s.value)
            ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_v, key_v, val]))
            return
        if obj_ty != "list":
            raise LowerError(f"unsupported stmt IndexAssign ({obj_ty})")
        if A.expr_type(s.value) == "float":
            raise LowerError("unsupported stmt IndexAssign (float element)")
        obj_v = _lower_expr(ctx, target.obj)
        idx_v = _lower_expr(ctx, target.index)
        addr = _list_elem_addr(ctx, obj_v, idx_v)
        val = _lower_expr(ctx, s.value)
        ctx.emit(IRInstr("store", None, [val, addr]))
        return

    if isinstance(s, A.TupleAssign):
        if any(isinstance(t, A.StarTarget) for t in s.targets):
            raise LowerError("unsupported stmt TupleAssign (starred target)")
        if not all(isinstance(t, A.Name) for t in s.targets):
            raise LowerError("unsupported stmt TupleAssign (non-Name target)")
        names = [t.name for t in s.targets]
        if len(s.values) == len(names):
            # Parallel form (a, b = 1, 2 / a, b = b, a): every rhs is
            # evaluated into a temp *before* any store, so swaps work.
            vals = [_lower_expr(ctx, v) for v in s.values]
            for name, val in zip(names, vals):
                ptr = ctx.ensure_slot(name, val.type)
                ctx.emit(IRInstr("store", None, [val, ptr]))
            return
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
                ptr = ctx.ensure_slot(name, elem_ty)
                ctx.emit(IRInstr("store", None, [val, ptr]))
            return
        raise LowerError("unsupported stmt TupleAssign (shape)")

    if isinstance(s, A.AttrAssign):
        # obj.name = value -> _abi_dict_set(obj, name, value); see the
        # A.Attr read path's comment for why this goes through a shim.
        obj_val = _lower_expr(ctx, s.obj)
        name = ctx.mctx.intern_str(s.name)
        key_ptr = ctx.tmp(PTR)
        ctx.emit(IRInstr("global_addr", key_ptr, [name]))
        val = _lower_expr(ctx, s.value)
        ctx.emit(IRInstr("call", None, ["_abi_dict_set", obj_val, key_ptr, val]))
        return

    if isinstance(s, A.Return):
        if s.value is None:
            zero = ctx.tmp(I64)
            ctx.emit(IRInstr("const", zero, [0]))
            ctx.emit(IRInstr("ret", None, [zero]))
        else:
            v = _lower_expr(ctx, s.value)
            ctx.emit(IRInstr("ret", None, [v]))
        return

    if isinstance(s, A.ExprStmt):
        _lower_expr(ctx, s.expr)
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
        end_b = ctx.new_block("whileend")

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        cond = _lower_truthy(ctx, s.test)
        ctx.emit(IRInstr("br.t", None, [cond, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        ctx.loop_stack.append((head_b.label, end_b.label))
        for st in s.body:
            _lower_stmt(ctx, st)
        ctx.loop_stack.pop()
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
        return

    if isinstance(s, A.For) and s.iter is not None:
        if s.targets:
            raise LowerError("unsupported stmt For (tuple-unpack targets)")
        if A.expr_type(s.iter) != "list":
            raise LowerError(f"unsupported stmt For (iterating {A.expr_type(s.iter)!r})")
        if isinstance(s.iter, A.ListLit):
            el_ty = s.iter.el_type
        elif isinstance(s.iter, A.Name):
            el_ty = ctx.slot_el_ty.get(s.iter.name, "int")
        else:
            el_ty = "int"
        if el_ty == "float":
            raise LowerError("unsupported stmt For (float list elements)")
        var_ty = ir_type_for(el_ty)

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
        end_b = ctx.new_block("forlistend")

        ctx.emit(IRInstr("br", None, [head_b.label]))
        ctx.switch_to(head_b)
        idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", idx_v, [idx_ptr]))
        cur_list_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", cur_list_v, [list_ptr]))
        len_addr = ctx.tmp(PTR)
        ctx.emit(IRInstr("gep", len_addr, [cur_list_v, _LIST_LEN_OFF]))
        len_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", len_v, [len_addr]))
        cond = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.lt", cond, [idx_v, len_v]))
        ctx.emit(IRInstr("br.t", None, [cond, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        # Reload the buffer pointer fresh each iteration (matching
        # codegen.py's _gen_for_list): an in-body append/extend call may
        # have reallocated it since the head block's check.
        body_list_v = ctx.tmp(PTR)
        ctx.emit(IRInstr("load", body_list_v, [list_ptr]))
        body_idx_v = ctx.tmp(I64)
        ctx.emit(IRInstr("load", body_idx_v, [idx_ptr]))
        addr = _list_elem_addr(ctx, body_list_v, body_idx_v)
        elem_v = ctx.tmp(var_ty)
        ctx.emit(IRInstr("load", elem_v, [addr]))
        var_ptr = ctx.ensure_slot(s.var, var_ty)
        ctx.emit(IRInstr("store", None, [elem_v, var_ptr]))

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

        ctx.switch_to(end_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
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

        var_ptr = ctx.ensure_slot(s.var, I64)
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
        end_b = ctx.new_block("forend")

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
        ctx.emit(IRInstr("br.t", None, [cond_pos, body_b.label, end_b.label]))

        var_v2 = ctx.tmp(I64)
        stop_v2 = ctx.tmp(I64)
        ctx.switch_to(neg_b)
        ctx.emit(IRInstr("load", var_v2, [var_ptr]))
        ctx.emit(IRInstr("load", stop_v2, [stop_ptr]))
        cond_neg = ctx.tmp(I64)
        ctx.emit(IRInstr("icmp.gt", cond_neg, [var_v2, stop_v2]))
        ctx.emit(IRInstr("br.t", None, [cond_neg, body_b.label, end_b.label]))

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

        ctx.switch_to(end_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
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

    raise LowerError(f"unsupported stmt {type(s).__name__}")


def lower_func(f: A.FuncDef, mctx: _ModuleCtx, *, visibility: str | None = None) -> IRFunc:
    ctx = _FuncCtx(mctx)
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

    return IRFunc(name=f.name, params=params, ret_type=I64, blocks=ctx.blocks, visibility=visibility)


def lower_module(mod: A.Module) -> IRModule:
    mctx = _ModuleCtx(frozenset(c.name for c in mod.classes), mod.ffi_funcs)
    funcs = [lower_func(f, mctx) for f in mod.funcs]
    # A user-defined top-level `def main():` already produces the entry
    # symbol; only synthesize one wrapping module-level statements when
    # there isn't one (the normal asmpython shape: a script with no
    # explicit main(), matching what codegen.py's emit_entry() assumes).
    if not any(f.name == "main" for f in mod.funcs):
        main_body = A.FuncDef(name="main", params=[], body=list(mod.body))
        funcs.append(lower_func(main_body, mctx, visibility="global"))
    return IRModule(funcs=funcs, data=mctx.data)
