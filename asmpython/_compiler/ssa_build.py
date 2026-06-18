"""Build SSA IR (ir.py) from the typed AST that sema.py has already
annotated. This is the second stage of the 2.0.0 ARM64 rewrite — see
docs/IR-DESIGN.md for the overall design and migration plan.

Scope note (see docs/IR-DESIGN.md "Migration strategy" and the
2026-06-17 design-session notes): most of asmpython's language surface
(lists, dicts, tuples, sets, comprehensions, f-strings, exceptions,
closures, match statements) already lowers — in the *existing*
direct-emission codegen.py — to calls into the `_runtime_*` helper
library, not to primitive arithmetic. That means most AST node kinds
translate to IR almost mechanically as `Op.CALL`/`Op.RAW_ASM`
instructions wrapping the same runtime-helper call sequence, rather
than needing bespoke new IR semantics each. The genuinely new
translation work is concentrated in a much smaller core: literals,
name/local reads and writes, arithmetic/comparison on primitive
int/float values, and control flow (if/while/for/return) — those get
real typed IR instructions (CONST, ADD, ICMP, CONDBR, ...). Everything
that currently calls a `_runtime_*` helper keeps doing so, just emitted
as an IR CALL instead of literal asm text, deferring the actual
per-helper ARM64 rewrite to the "port runtime helpers" migration step.

Built up incrementally, AST node kind by node kind, via the
`_EXPR_BUILDERS` / `_STMT_BUILDERS` dispatch tables at the bottom of
this file. Each builder is independently testable by constructing a
small `A.FuncDef`, building its IR, and checking the result's structure
(and, once X86_64Target lowering exists, checking compiled-and-run
output matches the existing direct-emission codegen on the same input).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional

from . import ast_nodes as A
from . import ir
from .ir import Kind, Op, Predicate, Value
from .ir_builder import BlockBuilder, new_block, new_function


class SSABuildError(Exception):
    """Raised for an AST construct ssa_build.py doesn't handle yet.

    Distinct from SemaError (errors.py) — this means "the IR builder's
    coverage doesn't extend here yet" during incremental development,
    not a user-facing diagnostic. Once coverage is complete for a given
    release this should never fire on sema-accepted input; if it does,
    that's a bug in the builder, not a rejected program.
    """


@dataclass
class FuncCtx:
    """Per-function state threaded through every statement/expression
    builder: the IR function under construction, the current block
    (mutated as control flow advances — callers must re-read `ctx.block`
    after building any sub-expression/sub-statement rather than holding
    a stale reference), frame-slot bookkeeping mirroring codegen.py's
    FuncInfo, and loop-exit targets for break/continue.

    `locals_` maps a source name to its IR representation: always
    `("mem", offset)` in this first cut (an `[rbp+offset]`-style frame
    slot, exactly matching today's codegen behavior) — see the module
    docstring on `FRAME_BASE` below for why register-promotion of locals
    is deferred rather than designed in from day one.
    """

    func: ir.Function
    block: ir.Block
    frame_offset: int = 0  # next free [rbp+N] slot, mirrors FuncInfo.offset
    locals_: dict[str, tuple] = dc_field(default_factory=dict)
    local_types: dict[str, str] = dc_field(default_factory=dict)
    # (continue_target, break_target) stack, innermost last — mirrors
    # codegen.py's Codegen.loop_labels.
    loop_targets: list[tuple[ir.Block, ir.Block]] = dc_field(default_factory=list)
    # Set of names this function (or its enclosing scope chain) exposes
    # as module globals — mirrors codegen.py's self.global_vars keys,
    # needed so a Name read/write of a global-declared name addresses
    # the module's .bss slot instead of a frame slot. Populated by the
    # caller (codegen integration step) from the same source codegen.py
    # already computes; ssa_build.py doesn't recompute it.
    global_names: set = dc_field(default_factory=set)

    def builder(self) -> BlockBuilder:
        return BlockBuilder(self.func, self.block)

    def alloc_slot(self, name: str, ty: str) -> int:
        self.frame_offset -= 8
        self.locals_[name] = ("mem", self.frame_offset)
        self.local_types[name] = ty
        return self.frame_offset

    def kind_of(self, ty: str) -> Kind:
        return Kind.FLOAT if ty == "float" else Kind.INT


# Pseudo-value representing the frame base (today's `rbp`). A LOAD/STORE
# against this with a constant offset expresses a local-variable
# read/write in the IR (see ir.py's module docstring and Op.LOAD/STORE) —
# there's no separate "local variable" IR instruction. Each target's
# lowering recognizes FRAME_BASE specially and emits a frame-relative
# addressing mode rather than treating it as a real pointer value that
# needs a register; it's a marker, not a runtime SSA value, which is why
# it's constructed directly rather than via `Function.new_value` (its
# id_=-1 can never collide with a real value, by construction, since
# `new_value` always starts a function's IDs at 0 and counts up).
FRAME_BASE = Value(id_=-1, kind=Kind.INT)

# Promoting hot locals from frame slots to registers (letting the
# allocator decide) is a real optimization opportunity flagged as an
# open question in docs/IR-DESIGN.md, deliberately deferred: the
# `("mem", offset)` tuple shape for `FuncCtx.locals_` leaves room to add
# an `("ssa", Value)` variant later for locals provably written exactly
# once per definition reaching each use (a standard mem2reg analysis),
# without needing to revisit every call site in this file — but that
# variant isn't implemented in this first cut, so `locals_` entries are
# always `("mem", offset)` today.


def _local_read(ctx: FuncCtx, name: str, ty: str) -> Value:
    _, offset = ctx.locals_[name]
    return ctx.builder().load(ctx.kind_of(ty), FRAME_BASE, offset=offset)


def _local_write(ctx: FuncCtx, name: str, value: Value) -> None:
    _, offset = ctx.locals_[name]
    ctx.builder().store(FRAME_BASE, value, offset=offset)


# ---- expression builders -----------------------------------------------------
#
# Each builder takes (ctx, expr) and returns the Value holding the
# expression's result. Builders may append new blocks to `ctx.func` and
# reassign `ctx.block` (e.g. short-circuit BoolOp evaluation needs new
# blocks) — callers must treat `ctx.block` as possibly-stale after any
# nested `_build_expr` call and re-fetch it via `ctx.builder()`.


def _build_intlit(ctx: FuncCtx, e: A.IntLit) -> Value:
    return ctx.builder().const(e.value)


def _build_floatlit(ctx: FuncCtx, e: A.FloatLit) -> Value:
    return ctx.builder().fconst(e.value)


def _build_strlit(ctx: FuncCtx, e: A.StrLit) -> Value:
    # String literals are heap/.rodata addresses, i.e. plain Kind.INT
    # values from the IR's point of view — the *string table* (interned
    # text -> label) is a target-lowering-time concern (today's
    # `self.intern_string`), not something the IR itself represents.
    # RAW_ASM is the right escape hatch here in the first cut: each
    # target's lowering interns the literal into its own data section
    # and substitutes the address-load instruction for that target.
    # (A dedicated Op.STRING_ADDR is the cleaner long-term shape — this
    # is marked for a follow-up once more of the literal/string-table
    # machinery is ported, to avoid prematurely committing to a string-
    # table API shape before more call sites are converted.)
    raise SSABuildError("StrLit: pending Op.STRING_ADDR design (follow-up)")


def _build_name(ctx: FuncCtx, e: A.Name) -> Value:
    # Mirrors codegen.py's gen_expr Name case (codegen.py:3398-3456), but
    # only the "plain local/global read" sub-case for this first cut —
    # the other ~7 sub-cases there (FFI consts, __name__/__file__
    # dunders, class-as-value, exception-as-value, bare-function-
    # reference, module-as-null, nonlocal-box deref) are each their own
    # follow-up since they depend on machinery (self.ffi_consts,
    # self.class_ids, ...) that isn't threaded into FuncCtx yet.
    if e.name in ctx.locals_:
        ty = ctx.local_types.get(e.name, "int")
        return _local_read(ctx, e.name, ty)
    if e.name in ctx.global_names:
        # Module globals live in a fixed .bss slot, addressed by symbol
        # rather than a frame offset — RAW_ASM per target until a real
        # Op.GLOBAL_ADDR (or similar) lands; see _build_strlit's note,
        # same shape of "defer the API until more call sites exist."
        raise SSABuildError(f"Name (module global) not yet implemented: {e.name!r}")
    raise SSABuildError(
        f"Name {e.name!r}: not a known local/global in this FuncCtx "
        "(FFI consts / dunders / class-as-value / etc. not yet implemented)"
    )


_EXPR_BUILDERS: dict[type, Callable[[FuncCtx, object], Value]] = {
    A.IntLit: _build_intlit,
    A.FloatLit: _build_floatlit,
    A.Name: _build_name,
}


def build_expr(ctx: FuncCtx, expr: A.Expr) -> Value:
    builder = _EXPR_BUILDERS.get(type(expr))
    if builder is None:
        raise SSABuildError(f"no IR builder for expression type {type(expr).__name__}")
    return builder(ctx, expr)


# ---- statement builders -------------------------------------------------------


def _build_assign(ctx: FuncCtx, s: A.Assign) -> None:
    value = build_expr(ctx, s.value)
    if s.target not in ctx.locals_:
        ty = A.expr_type(s.value)
        ctx.alloc_slot(s.target, ty)
    _local_write(ctx, s.target, value)


def _build_return(ctx: FuncCtx, s: A.Return) -> None:
    value = build_expr(ctx, s.value) if s.value is not None else None
    ctx.builder().ret(value)


_STMT_BUILDERS: dict[type, Callable[[FuncCtx, object], None]] = {
    A.Assign: _build_assign,
    A.Return: _build_return,
}


def build_stmt(ctx: FuncCtx, stmt: A.Stmt) -> None:
    builder = _STMT_BUILDERS.get(type(stmt))
    if builder is None:
        raise SSABuildError(f"no IR builder for statement type {type(stmt).__name__}")
    builder(ctx, stmt)


def build_stmts(ctx: FuncCtx, stmts: list) -> None:
    for s in stmts:
        build_stmt(ctx, s)
