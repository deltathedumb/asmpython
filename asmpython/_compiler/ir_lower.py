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
    ir_type_for,
)


class LowerError(Exception):
    pass


class _FuncCtx:
    def __init__(self) -> None:
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


def lower_func(f: A.FuncDef, *, visibility: str | None = None) -> IRFunc:
    ctx = _FuncCtx()
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
    funcs = [lower_func(f) for f in mod.funcs]
    # A user-defined top-level `def main():` already produces the entry
    # symbol; only synthesize one wrapping module-level statements when
    # there isn't one (the normal asmpython shape: a script with no
    # explicit main(), matching what codegen.py's emit_entry() assumes).
    if not any(f.name == "main" for f in mod.funcs):
        main_body = A.FuncDef(name="main", params=[], body=list(mod.body))
        funcs.append(lower_func(main_body, visibility="global"))
    return IRModule(funcs=funcs, data=[])
