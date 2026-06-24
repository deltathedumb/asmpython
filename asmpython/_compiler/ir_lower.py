"""Lower asmpython's AST (post-sema) to the SSA IR in ir.py, for handoff to
a uasm-shaped x86-64 backend (run_backend_codegen).

First-milestone scope only: int arithmetic, comparisons, if/while, return,
and calls between asmpython functions. No floats, no runtime calls (dict/
list/str/closures) yet -- those need the existing `_runtime_*` helpers
linked in via the legacy runtime archive, which is a separate, later step.
Intentionally narrow so the new pipeline (lowering -> uasm backend ->
object file -> link -> run) can be proven correct on its own before
carrying over the much larger dynamic-object surface that codegen.py
already handles by hand.

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


def _lower_expr(ctx: _FuncCtx, e: A.Expr) -> IRValue:
    if isinstance(e, A.IntLit):
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", v, [int(e.value)]))
        return v

    if isinstance(e, A.Name):
        ty = ctx.slot_ty.get(e.name, I64)
        ptr = ctx.ensure_slot(e.name, ty)
        v = ctx.tmp(ty)
        ctx.emit(IRInstr("load", v, [ptr]))
        return v

    if isinstance(e, A.BinOp):
        if e.op not in _BINOP:
            raise LowerError(f"unsupported binop {e.op!r}")
        a = _lower_expr(ctx, e.left)
        b = _lower_expr(ctx, e.right)
        v = ctx.tmp(I64)
        ctx.emit(IRInstr(_BINOP[e.op], v, [a, b]))
        return v

    if isinstance(e, A.Compare):
        # Chained comparison a < b < c -> (a < b) and (b < c); short-circuits
        # by only evaluating the next term once the previous one is true.
        result: IRValue | None = None
        operands = [_lower_expr(ctx, e.operands[0])]
        for i, op in enumerate(e.ops):
            if op not in _CMPOP:
                raise LowerError(f"unsupported compare op {op!r}")
            rhs = _lower_expr(ctx, e.operands[i + 1])
            operands.append(rhs)
            step = ctx.tmp(I64)
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
            fmt_name = ctx.mctx.intern_str("%s\n" if arg_ty == "str" else "%lld\n")
            fmt_ptr = ctx.tmp(PTR)
            ctx.emit(IRInstr("global_addr", fmt_ptr, [fmt_name]))
            ctx.emit(IRInstr("call", None, ["printf", fmt_ptr, val]))
        v = ctx.tmp(I64)
        ctx.emit(IRInstr("const", v, [0]))
        return v

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
        return

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
        cond = _lower_expr(ctx, s.test)
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
        cond = _lower_expr(ctx, s.test)
        ctx.emit(IRInstr("br.t", None, [cond, body_b.label, end_b.label]))

        ctx.switch_to(body_b)
        for st in s.body:
            _lower_stmt(ctx, st)
        ctx.emit(IRInstr("br", None, [head_b.label]))

        ctx.switch_to(end_b)
        for st in s.orelse:
            _lower_stmt(ctx, st)
        return

    raise LowerError(f"unsupported stmt {type(s).__name__}")


def lower_func(f: A.FuncDef, mctx: _ModuleCtx, *, visibility: str | None = None) -> IRFunc:
    ctx = _FuncCtx(mctx)
    entry = ctx.new_block("entry")
    ctx.switch_to(entry)

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
