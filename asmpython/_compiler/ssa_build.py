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
from .codegen import BUILTIN_EXC_IDS
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
    # Set of names that are plain module-level user functions (mirrors
    # codegen.py's self.funcs keys) — distinguishes a `Call` to a real
    # user-defined function (the genuinely-new IR-call case this file
    # implements) from the ~30 builtin-name special cases (print, len,
    # range, str(), etc.) that _gen_call dispatches on first, each of
    # which already calls a _runtime_* helper today and is deferred to
    # the CALL/RAW_ASM wrapping pass rather than reimplemented here.
    user_funcs: set = dc_field(default_factory=set)

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


# RawAsm target_text is keyed by (OS, ABI), not just architecture — Windows
# x86-64 (Win64 ABI: rcx/rdx/r8/r9, 32-byte shadow space) and Linux x86-64
# (SysV ABI: rdi/rsi/rdx/rcx/r8/r9, no shadow space) are the same
# architecture but need different text for anything that calls an external
# (libc) function directly, exactly mirroring today's WindowsCodegen vs.
# LinuxCodegen split (see codegen.py's _emit_strlen: "mov rcx, rax" on
# Windows, "mov rdi, rax" on Linux, for the same `call strlen`). A RawAsm
# site that's fully self-contained (an internal asm label, no external
# call) can use the same text for both keys, but still writes both
# explicitly — relying on an implicit fallback would silently break the
# day a self-contained helper changes to call something external.
_X86_64_KEYS = ("win64", "linux_x86_64")


def _build_runtime_raise(ctx: FuncCtx, msg: Value, exc_id: Value) -> None:
    # _runtime_raise(rax=msg, rbx=exc_id) -> never returns (codegen.py:8445:
    # "rax = exception message (string ptr), rbx = exception type id").
    # This is the internal rax/rbx convention, NOT the platform ABI - a
    # real bug existed here until this helper was introduced: the two
    # call sites (zero-division checks) used `b.call(Kind.NONE,
    # "_runtime_raise", [msg, exc_id])`, i.e. Op.CALL, which the IR
    # documents as using ABI-derived argument registers (rcx/rdx on
    # Win64, rdi/rsi on SysV) - wrong for a helper that reads rax/rbx by
    # convention like every other _runtime_* helper. Predates the
    # RAW_ASM argument-convention being resolved (it was written earlier,
    # before string operations needed RAW_ASM at all, and never
    # revisited). Fixed by routing through RAW_ASM like every other
    # _runtime_* call, consistent with _X86_64_KEYS' convention.
    ctx.builder().raw_asm(
        Kind.NONE, [msg, exc_id],
        {k: "call _runtime_raise" for k in _X86_64_KEYS},
    )


# ---- expression builders -----------------------------------------------------
#
# Each builder takes (ctx, expr) and returns the Value holding the
# expression's result. Builders may append new blocks to `ctx.func` and
# reassign `ctx.block` (e.g. short-circuit BoolOp evaluation needs new
# blocks) — callers must treat `ctx.block` as possibly-stale after any
# nested `_build_expr` call and re-fetch it via `ctx.builder()`.


# List header layout (codegen.py:8523-8526): [cap, len, buf_ptr], each an
# 8-byte field, then a separately-malloc'd buffer of `cap * 8` bytes
# (tuples reuse this exact layout). Mirrored here as plain offsets rather
# than named IR concepts since the IR's memory model is just "typed
# LOAD/STORE at a constant offset from a pointer value" (ir.py's module
# docstring) — a list header is no different from a frame slot to the IR,
# just a heap pointer instead of FRAME_BASE.
_LIST_CAP_OFF = 0
_LIST_LEN_OFF = 8
_LIST_BUF_OFF = 16
_LIST_HEADER_SIZE = 24


def _build_malloc(ctx: FuncCtx, n_bytes: int) -> Value:
    # codegen.py's _emit_malloc: a compile-time-constant size baked
    # directly into the RawAsm text (not passed via the args/register
    # convention - there's nothing to pass, malloc's one arg IS the
    # constant). Genuinely OS-specific arg register (Win64 rcx vs SysV rdi).
    return ctx.builder().raw_asm(
        Kind.INT, [],
        {
            "win64": f"mov rcx, {n_bytes}\ncall malloc",
            "linux_x86_64": f"mov rdi, {n_bytes}\ncall malloc",
        },
    )


def _build_listlit(ctx: FuncCtx, e: A.ListLit) -> Value:
    # Mirrors codegen.py's _gen_list_lit (codegen.py:8561-8589). codegen.py
    # parks the header pointer in a dedicated frame slot between the two
    # malloc calls and each element's (possibly call-emitting) evaluation,
    # since it can't push/pop a pointer across a `call` without breaking
    # 16-byte stack alignment. The IR doesn't need that trick at all: a
    # RawAsm/Call's result is a normal SSA Value that can be held live
    # across any number of later instructions (including more calls) by
    # construction - keeping it alive across calls is exactly what the
    # eventual register allocator's spill/reload logic is for, the same
    # way any other SSA value already works in this IR. No frame slot.
    #
    # Scoped to int/float elements only (no nested containers, no str
    # elements yet - str would need _runtime_str_concat_dup-style copying
    # semantics to consider, not just a wider element write).
    if e.el_type not in ("int", "float"):
        raise SSABuildError(f"ListLit with {e.el_type} elements: not yet wrapped")
    n = len(e.elems)
    cap = max(n, 4)
    header = _build_malloc(ctx, _LIST_HEADER_SIZE)
    b = ctx.builder()
    cap_const = b.const(cap)
    b.store(header, cap_const, offset=_LIST_CAP_OFF)
    b = ctx.builder()
    len_const = b.const(n)
    b.store(header, len_const, offset=_LIST_LEN_OFF)
    buf = _build_malloc(ctx, cap * 8)
    b = ctx.builder()
    b.store(header, buf, offset=_LIST_BUF_OFF)
    # No int->float promotion needed here, unlike arithmetic: sema rejects
    # mixed-type list literals outright (SemaError "mixed list element
    # types"), so every element's static type already equals e.el_type
    # exactly when el_type is "int" or "float" — codegen.py's
    # _gen_list_lit (codegen.py:8580-8588) doesn't promote either, it
    # just trusts that invariant. Matched here rather than reusing
    # _build_as_float, which would silently paper over a real sema
    # contract violation if it were ever wrong.
    for i, el in enumerate(e.elems):
        v = build_expr(ctx, el)
        b = ctx.builder()
        b.store(buf, v, offset=i * 8)
    return header


def _build_subscript(ctx: FuncCtx, e: A.Subscript) -> Value:
    # Mirrors codegen.py's _gen_subscript (codegen.py:9253) list-index case
    # (codegen.py:9316-9353) only — dict keys, string indexing, slices
    # (A.Slice index), and instance __getitem__ are all deferred (each is
    # its own dispatch branch in codegen.py, not a variation on this one).
    # Scoped to int/float list elements, matching ListLit's current scope.
    if isinstance(e.index, A.Slice):
        raise SSABuildError("Subscript with a Slice index: not yet wrapped")
    obj_t = A.expr_type(e.obj)
    if obj_t != "list":
        raise SSABuildError(f"Subscript on {obj_t}: not yet wrapped")
    el_t = e.inferred_type
    if el_t not in ("int", "float"):
        raise SSABuildError(f"Subscript yielding {el_t}: not yet wrapped")

    header = build_expr(ctx, e.obj)
    raw_idx = build_expr(ctx, e.index)

    # Negative-index wraparound: idx < 0 -> idx += len. A real branch (not
    # a branchless formula) to match codegen.py's structure exactly
    # (codegen.py:9320-9329's "jns pos / add rcx, [...]"), expressed as an
    # IfExp-shaped diamond merged via phi rather than codegen's jump-to-
    # shared-tail-with-rcx-preset.
    neg_blk, _ = new_block(ctx.func, "idx_neg")
    pos_blk, _ = new_block(ctx.func, "idx_pos")
    norm_blk, _ = new_block(ctx.func, "idx_norm")
    b = ctx.builder()
    zero = b.const(0)
    is_neg = b.icmp(Predicate.LT, raw_idx, zero)
    b.condbr(is_neg, neg_blk, pos_blk)
    neg_blk.preds.append(ctx.block)
    pos_blk.preds.append(ctx.block)

    ctx.block = neg_blk
    b = ctx.builder()
    list_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
    b = ctx.builder()
    wrapped_idx = b.add(raw_idx, list_len)
    b.br(norm_blk)
    norm_blk.preds.append(neg_blk)

    ctx.block = pos_blk
    ctx.builder().br(norm_blk)
    norm_blk.preds.append(pos_blk)

    ctx.block = norm_blk
    norm_idx = ctx.builder().phi(Kind.INT)
    ctx.builder().add_incoming(norm_idx, wrapped_idx)
    ctx.builder().add_incoming(norm_idx, raw_idx)

    # Bounds check: 0 <= norm_idx < len (codegen.py:9330-9352). Short-
    # circuit, same shape as codegen's "js oob / cmp+jge oob" sequential
    # jumps (and as _build_compare's chained-comparison short-circuit) -
    # an AND-of-two-booleans would be semantically equivalent but always
    # evaluates the second comparison even when the first already fails,
    # unlike codegen.py's structure this is meant to mirror.
    check_len_blk, _ = new_block(ctx.func, "idx_check_len")
    ok_blk, _ = new_block(ctx.func, "idx_ok")
    oob_blk, _ = new_block(ctx.func, "idx_oob")
    b = ctx.builder()
    zero2 = b.const(0)
    nonneg = b.icmp(Predicate.GE, norm_idx, zero2)
    b.condbr(nonneg, check_len_blk, oob_blk)
    check_len_blk.preds.append(norm_blk)
    oob_blk.preds.append(norm_blk)

    ctx.block = check_len_blk
    b = ctx.builder()
    list_len2 = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
    b = ctx.builder()
    in_range = b.icmp(Predicate.LT, norm_idx, list_len2)
    b.condbr(in_range, ok_blk, oob_blk)
    ok_blk.preds.append(check_len_blk)
    oob_blk.preds.append(check_len_blk)

    ctx.block = oob_blk
    msg = ctx.builder().string_addr("list index out of range")
    exc_id = ctx.builder().const(BUILTIN_EXC_IDS["IndexError"])
    _build_runtime_raise(ctx, msg, exc_id)
    ctx.builder().ret(None)  # unreachable, same convention as the zero-division raises

    ctx.block = ok_blk
    b = ctx.builder()
    buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
    b = ctx.builder()
    byte_off = b.shl(norm_idx, b.const(3))  # idx * 8
    b = ctx.builder()
    elem_ptr = b.add(buf, byte_off)
    b = ctx.builder()
    el_kind = Kind.FLOAT if el_t == "float" else Kind.INT
    return b.load(el_kind, elem_ptr, offset=0)


def _build_intlit(ctx: FuncCtx, e: A.IntLit) -> Value:
    return ctx.builder().const(e.value)


def _build_floatlit(ctx: FuncCtx, e: A.FloatLit) -> Value:
    return ctx.builder().fconst(e.value)


def _build_strlit(ctx: FuncCtx, e: A.StrLit) -> Value:
    # String literals are heap/.rodata addresses, i.e. plain Kind.INT
    # values from the IR's point of view. Op.STRING_ADDR carries the
    # raw literal text; each target's lowering is responsible for
    # interning it (deduplicating identical literals program-wide into
    # one .rodata entry, exactly like today's Codegen.intern_string) —
    # see ir.py's Op.STRING_ADDR docstring for why that bookkeeping
    # belongs at the target-lowering stage rather than here.
    return ctx.builder().string_addr(e.value)


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


# op string -> (int Op, float Op-or-None). Mirrors codegen.py's
# _emit_binop_inline (codegen.py:11310) dispatch table, primitive-type
# case only — dunder/string/list/dict/set BinOp lowering (the bulk of
# _gen_binop, codegen.py:10982) is deferred to the CALL/RAW_ASM wrapping
# pass since it already goes through _runtime_* helpers today.
_SIMPLE_BINOPS: dict[str, tuple[Op, Optional[Op]]] = {
    "+": (Op.ADD, Op.FADD),
    "-": (Op.SUB, Op.FSUB),
    "*": (Op.MUL, Op.FMUL),
    "&": (Op.AND, None),   # bitwise: int-only, sema rejects float operands
    "|": (Op.OR, None),
    "^": (Op.XOR, None),
    "<<": (Op.SHL, None),
    ">>": (Op.SAR, None),  # Python >> is arithmetic (sign-preserving) shift
}

_COMPARE_PRED: dict[str, Predicate] = {
    "==": Predicate.EQ,
    "!=": Predicate.NE,
    "<": Predicate.LT,
    "<=": Predicate.LE,
    ">": Predicate.GT,
    ">=": Predicate.GE,
    # `is`/`is not`: identity-as-bit-equality, same lowering as ==/!= given
    # asmpython's uniform 8-byte value representation (codegen.py:11391-95).
    "is": Predicate.EQ,
    "is not": Predicate.NE,
}


def _build_check_float_nonzero_divisor(ctx: FuncCtx, divisor: Value) -> None:
    """Raise ZeroDivisionError if `divisor` (an F64 value) is 0.0.
    Mirrors codegen.py's `_emit_check_float_nonzero_divisor`
    (codegen.py:~11260-11285), shared by float `/`, `//`, and `%`.

    A NaN divisor is NOT zero (dividing by NaN is valid IEEE-754 — it
    just produces NaN, not an exception) — codegen.py handles this by
    checking ucomisd's parity flag (PF, set on an unordered/NaN compare)
    and treating "unordered" as "proceed, don't raise." This builder
    gets the same behavior for free from FCmp.NE's IEEE
    unordered-compares-as-not-equal semantics (see _build_truthy_branch's
    docstring for the same reasoning applied to truthiness) — no
    separate NaN check needed at the IR level.
    """
    nonzero_blk, nonzero_b = new_block(ctx.func, "fdiv_nonzero")
    raise_blk, raise_b = new_block(ctx.func, "fdiv_raise")
    b = ctx.builder()
    zero = b.fconst(0.0)
    is_nonzero = b.fcmp(Predicate.NE, divisor, zero)
    b.condbr(is_nonzero, nonzero_blk, raise_blk)
    nonzero_blk.preds.append(ctx.block)
    raise_blk.preds.append(ctx.block)

    ctx.block = raise_blk
    msg = ctx.builder().string_addr("division by zero")
    exc_id = ctx.builder().const(BUILTIN_EXC_IDS["ZeroDivisionError"])
    _build_runtime_raise(ctx, msg, exc_id)
    ctx.builder().ret(None)  # unreachable; see _build_int_floordiv_mod's note on this

    ctx.block = nonzero_blk


def _build_int_pow(ctx: FuncCtx, e: A.BinOp) -> Value:
    """Integer exponentiation: `result = 1; while exp > 0: result *=
    base; exp -= 1`, mirroring codegen.py's inline pow loop
    (codegen.py:11363-11380) exactly (including its int-only `**`
    semantics — no negative-exponent or overflow handling, matching
    what the existing direct-emission codegen already does).

    Uses temporary frame slots for the loop-carried `result`/`exp`
    values (the same approach `_build_for_range` uses for its loop
    variable) rather than hand-rolling phi nodes directly — simpler and
    consistent with this file's established style for loop-carried
    state, at the cost of a memory round-trip per iteration that a
    later register-promotion pass (see docs/IR-DESIGN.md's open
    questions) could eliminate.
    """
    base_v = build_expr(ctx, e.left)
    exp_v = build_expr(ctx, e.right)
    result_name = f"__pow_result_{id(e)}"
    exp_name = f"__pow_exp_{id(e)}"
    base_name = f"__pow_base_{id(e)}"
    ctx.alloc_slot(result_name, "int")
    ctx.alloc_slot(exp_name, "int")
    ctx.alloc_slot(base_name, "int")
    one = ctx.builder().const(1)
    _local_write(ctx, result_name, one)
    _local_write(ctx, exp_name, exp_v)
    _local_write(ctx, base_name, base_v)

    loop_blk, loop_b = new_block(ctx.func, "pow_loop")
    body_blk, body_b = new_block(ctx.func, "pow_body")
    end_blk, end_b = new_block(ctx.func, "pow_end")

    entry_blk = ctx.block
    ctx.builder().br(loop_blk)
    loop_blk.preds.append(entry_blk)

    ctx.block = loop_blk
    exp_read = _local_read(ctx, exp_name, "int")
    b = ctx.builder()
    zero = b.const(0)
    exp_done = b.icmp(Predicate.LE, exp_read, zero)
    b.condbr(exp_done, end_blk, body_blk)
    end_blk.preds.append(loop_blk)
    body_blk.preds.append(loop_blk)

    ctx.block = body_blk
    result_read = _local_read(ctx, result_name, "int")
    b = ctx.builder()
    base_read = _local_read(ctx, base_name, "int")
    b = ctx.builder()
    new_result = b.mul(result_read, base_read)
    _local_write(ctx, result_name, new_result)
    exp_read2 = _local_read(ctx, exp_name, "int")
    b = ctx.builder()
    one2 = b.const(1)
    new_exp = b.sub(exp_read2, one2)
    _local_write(ctx, exp_name, new_exp)
    ctx.builder().br(loop_blk)
    loop_blk.preds.append(ctx.block)

    ctx.block = end_blk
    return _local_read(ctx, result_name, "int")


def _build_int_floordiv_mod(ctx: FuncCtx, e: A.BinOp, lt: str, rt: str) -> Value:
    """`a // b` / `a % b` on plain int operands. Mirrors codegen.py's
    `_emit_binop_inline` (op in ("//", "%"), codegen.py:11318-11349):

    1. Zero-check the divisor; raise ZeroDivisionError if it's 0
       (codegen.py:11319-30's `test rbx, rbx` / `jnz nonzero` / raise).
    2. SDIV/SREM truncate toward zero, but Python's // and % floor
       toward -inf — when the remainder is nonzero and its sign differs
       from the divisor's, adjust: quotient -= 1, remainder += divisor
       (codegen.py:11331-46's cqo/idiv + sign-mismatch adjustment,
       expressed here as ICMP/CONDBR + a merge phi instead of raw
       flags-register jcc chains).
    """
    left = build_expr(ctx, e.left)
    right = build_expr(ctx, e.right)

    nonzero_blk, nonzero_b = new_block(ctx.func, "divmod_nonzero")
    raise_blk, raise_b = new_block(ctx.func, "divmod_raise")
    b = ctx.builder()
    zero = b.const(0)
    is_zero = b.icmp(Predicate.EQ, right, zero)
    b.condbr(is_zero, raise_blk, nonzero_blk)
    raise_blk.preds.append(ctx.block)
    nonzero_blk.preds.append(ctx.block)

    ctx.block = raise_blk
    msg = ctx.builder().string_addr("division by zero")
    exc_id = ctx.builder().const(BUILTIN_EXC_IDS["ZeroDivisionError"])
    # _runtime_raise never returns (it longjmps to the nearest handler,
    # or prints+exits if none is installed) - codegen.py never emits
    # anything after this call on this path either. The IR still needs
    # a terminator for this block per Function.validate, so RET with no
    # value stands in for "unreachable" until the IR has a dedicated
    # marker for it (a real Op.UNREACHABLE is a reasonable follow-up
    # once more exception-handling call sites exist to validate the
    # shape against).
    _build_runtime_raise(ctx, msg, exc_id)
    ctx.builder().ret(None)

    ctx.block = nonzero_blk
    b = ctx.builder()
    quot = b.sdiv(left, right)
    b = ctx.builder()
    rem = b.srem(left, right)

    # Sign-mismatch check: remainder nonzero AND (remainder XOR divisor) < 0
    # (i.e. they have different signs) -> adjust.
    rem_nonzero_blk, rem_nonzero_b = new_block(ctx.func, "divmod_remnz")
    adjust_blk, adjust_b = new_block(ctx.func, "divmod_adjust")
    merge_blk, merge_b = new_block(ctx.func, "divmod_merge")
    b = ctx.builder()
    rem_is_zero = b.icmp(Predicate.EQ, rem, zero)
    b.condbr(rem_is_zero, merge_blk, rem_nonzero_blk)
    merge_blk.preds.append(ctx.block)
    rem_nonzero_blk.preds.append(ctx.block)

    ctx.block = rem_nonzero_blk
    b = ctx.builder()
    sign_xor = b.xor(rem, right)
    b = ctx.builder()
    zero2 = b.const(0)
    signs_differ = b.icmp(Predicate.LT, sign_xor, zero2)
    b.condbr(signs_differ, adjust_blk, merge_blk)
    adjust_blk.preds.append(ctx.block)
    merge_blk.preds.append(ctx.block)

    ctx.block = adjust_blk
    b = ctx.builder()
    one = b.const(1)
    adj_quot = b.sub(quot, one)
    adj_rem = b.add(rem, right)
    b.br(merge_blk)
    merge_blk.preds.append(ctx.block)

    ctx.block = merge_blk
    b = ctx.builder()
    final_quot = b.phi(Kind.INT)
    final_rem = b.phi(Kind.INT)
    # Two of the three incoming edges (the rem-is-zero short-circuit and
    # the signs-agree case) carry the *unadjusted* quot/rem; only the
    # adjust_blk edge carries the adjusted pair. add_incoming order must
    # match merge_blk.preds' append order above: [from rem_is_zero check,
    # from signs_differ-false, from adjust_blk] - i.e. (quot, rem),
    # (quot, rem), (adj_quot, adj_rem).
    for q, r in ((quot, rem), (quot, rem), (adj_quot, adj_rem)):
        ctx.builder().add_incoming(final_quot, q)
        ctx.builder().add_incoming(final_rem, r)
    return final_quot if e.op == "//" else final_rem


def _build_binop(ctx: FuncCtx, e: A.BinOp) -> Value:
    lt, rt = A.expr_type(e.left), A.expr_type(e.right)
    if lt == "str" and rt == "str" and e.op == "+":
        # _runtime_str_concat(rax=a, rbx=b) -> rax (freshly allocated
        # concat) — codegen.py:2328-2332/5726-5745. Sema only allows str+str
        # (no int/float coercion), so this is the only string BinOp case.
        left = build_expr(ctx, e.left)
        right = build_expr(ctx, e.right)
        # Self-contained internal label (no external call), same text both
        # OSes — see _X86_64_KEYS' docstring (defined near _build_str_eq).
        return ctx.builder().raw_asm(
            Kind.INT, [left, right],
            {k: "call _runtime_str_concat" for k in _X86_64_KEYS},
        )
    if lt.startswith("instance:") or rt.startswith("instance:") or "str" in (lt, rt) or lt in (
        "list", "dict", "set", "tuple",
    ) or rt in ("list", "dict", "set", "tuple"):
        raise SSABuildError(
            f"BinOp {e.op!r} on {lt}/{rt}: dunder/string/container lowering "
            "not yet wrapped (deferred to the CALL/RAW_ASM pass)"
        )
    is_float = "float" in (lt, rt) or e.op == "/"  # true division always float
    b = ctx.builder()
    if e.op == "/":
        left = _build_as_float(ctx, e.left, lt)
        right = _build_as_float(ctx, e.right, rt)
        b = ctx.builder()
        _build_check_float_nonzero_divisor(ctx, right)
        b = ctx.builder()
        return b.fdiv(left, right)
    if e.op in ("//", "%"):
        if is_float:
            left = _build_as_float(ctx, e.left, lt)
            right = _build_as_float(ctx, e.right, rt)
            b = ctx.builder()
            _build_check_float_nonzero_divisor(ctx, right)
            b = ctx.builder()
            if e.op == "%":
                # libc fmod(double, double) (codegen.py:11302-11304).
                return b.call(Kind.FLOAT, "fmod", [left, right])
            # Float // : divide then round toward -inf (codegen.py's
            # `roundsd xmm0, xmm0, 1`, mode 1 = floor). divsd's quotient
            # already needs no truncate-then-adjust dance the way the
            # int SDIV/SREM path does — floor-rounding the raw quotient
            # directly gives Python's floor-division semantics. Calls
            # libc floor() rather than inventing a dedicated IR op for
            # one specific SSE rounding mode, keeping this consistent
            # with how pow()/fmod() are already handled as plain CALLs.
            quotient = b.fdiv(left, right)
            b = ctx.builder()
            return b.call(Kind.FLOAT, "floor", [quotient])
        return _build_int_floordiv_mod(ctx, e, lt, rt)
    if e.op == "**":
        if is_float:
            # Float ** is just libc pow(double, double) (codegen.py:11305-
            # 11306) - far simpler than the int path's loop, since pow()
            # already implements the semantics directly.
            left = _build_as_float(ctx, e.left, lt)
            right = _build_as_float(ctx, e.right, rt)
            b = ctx.builder()
            return b.call(Kind.FLOAT, "pow", [left, right])
        return _build_int_pow(ctx, e)
    if e.op not in _SIMPLE_BINOPS:
        raise SSABuildError(f"BinOp {e.op!r}: not yet implemented")
    int_op, float_op = _SIMPLE_BINOPS[e.op]
    if is_float:
        if float_op is None:
            raise SSABuildError(f"BinOp {e.op!r}: float operands not supported (sema should reject)")
        left = _build_as_float(ctx, e.left, lt)
        right = _build_as_float(ctx, e.right, rt)
        b = ctx.builder()
        return getattr(b, _OP_TO_BUILDER_METHOD[float_op])(left, right)
    left = build_expr(ctx, e.left)
    right = build_expr(ctx, e.right)
    b = ctx.builder()
    return getattr(b, _OP_TO_BUILDER_METHOD[int_op])(left, right)


_OP_TO_BUILDER_METHOD: dict[Op, str] = {
    Op.ADD: "add", Op.SUB: "sub", Op.MUL: "mul",
    Op.AND: "and_", Op.OR: "or_", Op.XOR: "xor",
    Op.SHL: "shl", Op.SAR: "sar",
    Op.FADD: "fadd", Op.FSUB: "fsub", Op.FMUL: "fmul", Op.FDIV: "fdiv",
}


def _build_as_float(ctx: FuncCtx, e: A.Expr, ty: str) -> Value:
    """Build `e` and promote to Kind.FLOAT if it's a plain int, mirroring
    codegen.py's `_gen_expr_as_float` int->float promotion at binop/
    comparison sites where Python implicitly widens (`1 + 2.0`)."""
    v = build_expr(ctx, e)
    if ty == "float":
        return v
    return ctx.builder().sitofp(v)


def _build_unaryop(ctx: FuncCtx, e: A.UnaryOp) -> Value:
    operand_t = A.expr_type(e.operand)
    if operand_t.startswith("instance:") or operand_t in ("str", "list", "dict", "set", "tuple"):
        raise SSABuildError(f"UnaryOp {e.op!r} on {operand_t}: not yet wrapped")
    v = build_expr(ctx, e.operand)
    b = ctx.builder()
    if e.op == "-":
        return b.fneg(v) if operand_t == "float" else b.neg(v)
    if e.op == "~":
        return b.not_(v)
    if e.op == "not":
        # Truthiness-of-a-primitive: int 0/1, str/list/dict/etc. truthiness
        # (empty-check) is deferred — see the str/container guard above,
        # which already rejects non-primitive operands before reaching here.
        zero = b.const(0) if operand_t != "float" else b.fconst(0.0)
        pred = Predicate.EQ
        return b.fcmp(pred, v, zero) if operand_t == "float" else b.icmp(pred, v, zero)
    raise SSABuildError(f"UnaryOp {e.op!r}: not yet implemented")


def _build_str_eq(ctx: FuncCtx, left: Value, right: Value) -> Value:
    # _runtime_str_eq(rax=a, rbx=b) -> rax (1/0), NULL-safe (None compares
    # equal to None, unequal to a real string) — codegen.py:6130-6145.
    # Self-contained internal label (no external call), same text both OSes.
    return ctx.builder().raw_asm(
        Kind.INT, [left, right],
        {k: "call _runtime_str_eq" for k in _X86_64_KEYS},
    )


def _build_compare(ctx: FuncCtx, e: A.Compare) -> Value:
    # Mirrors codegen.py's _gen_compare (codegen.py:11427), primitive
    # int/float case plus string ==/!= (the only string comparison wrapped
    # so far — _runtime_str_eq is a simple two-arg-in/one-result-out RAW_ASM
    # site, codegen.py:11526-11535). `in`/`not in`, dunder __eq__/__lt__
    # dispatch, string </<=/>/>= (needs _runtime_str_cmp, a separate
    # helper), and chained string comparisons are all still deferred.
    if any(op in ("in", "not in") for op in e.ops):
        raise SSABuildError("Compare 'in'/'not in': not yet wrapped")
    operand_types = [A.expr_type(o) for o in e.operands]
    if all(t == "str" for t in operand_types):
        if len(e.ops) != 1 or e.ops[0] not in ("==", "!="):
            raise SSABuildError("Chained or ordering (</<=/>/>=) string Compare: not yet wrapped")
        left = build_expr(ctx, e.operands[0])
        right = build_expr(ctx, e.operands[1])
        eq = _build_str_eq(ctx, left, right)
        if e.ops[0] == "==":
            return eq
        b = ctx.builder()
        one = b.const(1)
        return b.xor(eq, one)
    if any(t.startswith("instance:") or t in ("str", "list", "dict", "set", "tuple") for t in operand_types):
        raise SSABuildError("Compare on non-primitive operand: not yet wrapped")
    is_float = not all(op in ("is", "is not") for op in e.ops) and "float" in operand_types

    def _operand(i: int) -> Value:
        return _build_as_float(ctx, e.operands[i], operand_types[i]) if is_float else build_expr(ctx, e.operands[i])

    if len(e.ops) == 1:
        left = _operand(0)
        right = _operand(1)
        b = ctx.builder()
        pred = _COMPARE_PRED[e.ops[0]]
        return b.fcmp(pred, left, right) if is_float else b.icmp(pred, left, right)

    # Chained comparison (`a < b < c`): short-circuits to False as soon as
    # one link fails, matching codegen.py:11597-11622's false_lbl/end_lbl
    # branch structure, expressed here as real IR blocks merged via a phi
    # instead of jump-to-shared-tail-with-rax-preset.
    false_blk, false_b = new_block(ctx.func, "cmp_false")
    merge_blk, merge_b = new_block(ctx.func, "cmp_merge")
    left = _operand(0)
    for i, op in enumerate(e.ops):
        right = _operand(i + 1)
        b = ctx.builder()
        pred = _COMPARE_PRED[op]
        ok = b.fcmp(pred, left, right) if is_float else b.icmp(pred, left, right)
        cont_blk, cont_b = new_block(ctx.func, "cmp_cont")
        b.condbr(ok, cont_blk, false_blk)
        false_blk.preds.append(ctx.block)
        ctx.block = cont_blk
        left = right
    # Every link held: fall through to merge with 1.
    true_const = ctx.builder().const(1)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(ctx.block)
    ctx.block = false_blk
    false_const = ctx.builder().const(0)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(false_blk)
    ctx.block = merge_blk
    result = ctx.builder().phi(Kind.INT)
    ctx.builder().add_incoming(result, true_const)
    ctx.builder().add_incoming(result, false_const)
    return result


def _build_len(ctx: FuncCtx, e: A.Call) -> Value:
    # Mirrors codegen.py:11961-11981 — len()'s dispatch depends entirely on
    # the argument's static type. list/tuple/dict/set/instance.__len__ are
    # deferred (each needs a container layout or method dispatch this IR
    # doesn't have yet); string is the only case wrapped so far.
    arg = e.args[0]
    t = A.expr_type(arg)
    if t != "str":
        raise SSABuildError(f"len() of {t}: not yet wrapped")
    v = build_expr(ctx, arg)
    # libc strlen(ptr) -> length. Genuinely OS-specific text (Win64 passes
    # the first integer arg in rcx, SysV in rdi) — see codegen.py's
    # _emit_strlen and the _X86_64_KEYS docstring above.
    return ctx.builder().raw_asm(
        Kind.INT, [v],
        {
            "win64": "mov rcx, rax\ncall strlen",
            "linux_x86_64": "mov rdi, rax\ncall strlen",
        },
    )


def _build_str_of_int(ctx: FuncCtx, e: A.Call) -> Value:
    # str(x) where x is a plain (non-bool, non-None) int — codegen.py:
    # 12011-12013's final-else fallback. bool/None/float/str/instance/
    # container all have their own dispatch (codegen.py:11988-12010) and
    # are deferred: bool/None need is_bool_expr/is_none_expr-driven
    # branching to a different static string, float's _emit_float_to_str
    # has internal NaN/inf-detection control flow on Windows (the kind of
    # RawAsm-can't-safely-have-local-labels case docs/IR-DESIGN.md now
    # documents — needs real typed IR blocks, not a single RawAsm, when
    # it's done), and instance dispatch needs method resolution.
    arg = e.args[0]
    if A.expr_type(arg) != "int" or A.is_bool_expr(arg) or A.is_none_expr(arg):
        raise SSABuildError("str() of non-plain-int: not yet wrapped")
    v = build_expr(ctx, arg)
    b = ctx.builder()
    # sprintf(itoa_str_buf, fmt, v) -> rax = ptr into the shared static
    # buffer (codegen.py:167-175/116-125), then _runtime_str_concat_dup
    # copies out to a fresh allocation so the result doesn't alias the
    # buffer if a caller stores it (e.g. `[str(x) for x in xs]`).
    # Genuinely OS-specific: different arg registers (Win64 rcx/rdx/r8 vs
    # SysV rdi/rsi/rdx), different format-string label name, and SysV's
    # variadic-call convention requires AL = vector-register count (0
    # here, no float args) while Win64's doesn't.
    sprintf_result = b.raw_asm(
        Kind.INT, [v],
        {
            "win64": "\n".join([
                "mov r8, rax",
                "lea rdx, [fmt_int_only]",
                "lea rcx, [itoa_str_buf]",
                "call sprintf",
                "lea rax, [itoa_str_buf]",
            ]),
            "linux_x86_64": "\n".join([
                "mov rdx, rax",
                "lea rsi, [fmt_int]",
                "lea rdi, [itoa_str_buf]",
                "xor rax, rax",
                "call sprintf",
                "lea rax, [itoa_str_buf]",
            ]),
        },
    )
    b = ctx.builder()
    return b.raw_asm(
        Kind.INT, [sprintf_result],
        {k: "call _runtime_str_concat_dup" for k in _X86_64_KEYS},
    )


def _build_call(ctx: FuncCtx, e: A.Call) -> Value:
    # Mirrors codegen.py's _gen_call (codegen.py:11915) fallback case
    # (codegen.py:12948-12953) reached after ~30 builtin-name special
    # cases (print, len, range, str(), id(), ...) and the closure/
    # free-variable call path are checked and don't match — i.e. a
    # plain call to a real user-defined module-level function with
    # sema-normalized positional args (defaults filled, kwargs placed,
    # varargs packed; see sema.py's _bind_args). That's the genuinely
    # new IR-call case this builder implements; builtins are deferred
    # to the CALL/RAW_ASM wrapping pass since each already dispatches
    # to a _runtime_* helper today, and closures (functions captured as
    # values, called indirectly through a pointer) are a separate
    # follow-up — see ir.py's Op.CALL docstring for the is_indirect
    # mechanism this will eventually use.
    #
    # len() is the first builtin wrapped (see _build_len) — checked first,
    # matching codegen.py's _gen_call dispatch order exactly (codegen.py:
    # 11921-11980 checks every builtin name before ever consulting
    # self.funcs). A user function named "len" would be unreachable via a
    # call expression either way — same behavior as today, not a new
    # restriction introduced here.
    if e.func == "len":
        return _build_len(ctx, e)
    if e.func == "str":
        return _build_str_of_int(ctx, e)
    if e.func not in ctx.user_funcs:
        raise SSABuildError(
            f"Call to {e.func!r}: not a known user function in this FuncCtx "
            "(builtins/FFI/closures not yet implemented)"
        )
    args = [build_expr(ctx, a) for a in e.args]
    kind = ctx.kind_of(e.inferred_type)
    return ctx.builder().call(kind, e.func, args)


def _build_boolop(ctx: FuncCtx, e: A.BoolOp) -> Value:
    # Mirrors codegen.py's _gen_boolop (codegen.py:11739): short-circuit,
    # Python VALUE semantics, not boolean-normalized — `a or b` yields a's
    # own value when truthy (not 1), `a and b` yields a when falsy (not 0).
    # Only meaningful here for primitive int/float operands (str/list/
    # instance truthiness is deferred, same as _build_truthy_branch).
    lt, rt = A.expr_type(e.left), A.expr_type(e.right)
    if lt.startswith("instance:") or rt.startswith("instance:") or lt in (
        "str", "list", "dict", "set", "tuple",
    ) or rt in ("str", "list", "dict", "set", "tuple"):
        raise SSABuildError(f"BoolOp {e.op!r} on {lt}/{rt}: not yet wrapped")
    is_float = lt == "float" or rt == "float"
    kind = Kind.FLOAT if is_float else Kind.INT

    left = _build_as_float(ctx, e.left, lt) if is_float else build_expr(ctx, e.left)
    short_circuit_blk, _ = new_block(ctx.func, "bool_short")
    eval_right_blk, _ = new_block(ctx.func, "bool_right")
    merge_blk, _ = new_block(ctx.func, "bool_merge")
    test_blk = ctx.block

    b = ctx.builder()
    zero = b.fconst(0.0) if is_float else b.const(0)
    left_truthy = b.fcmp(Predicate.NE, left, zero) if is_float else b.icmp(Predicate.NE, left, zero)
    if e.op == "and":
        # Left falsy -> short-circuit (result is left); left truthy -> eval right.
        b.condbr(left_truthy, eval_right_blk, short_circuit_blk)
    else:
        # Left truthy -> short-circuit (result is left); left falsy -> eval right.
        b.condbr(left_truthy, short_circuit_blk, eval_right_blk)
    short_circuit_blk.preds.append(test_blk)
    eval_right_blk.preds.append(test_blk)

    # short_circuit_blk just forwards `left` to the merge — still needs its
    # own terminator (Function.validate rejects empty blocks).
    ctx.block = short_circuit_blk
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(short_circuit_blk)

    ctx.block = eval_right_blk
    right = _build_as_float(ctx, e.right, rt) if is_float else build_expr(ctx, e.right)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(ctx.block)

    ctx.block = merge_blk
    result = ctx.builder().phi(kind)
    # add_incoming order must match merge_blk.preds' append order above:
    # [short_circuit_blk carrying `left`, eval_right_blk's exit carrying `right`].
    ctx.builder().add_incoming(result, left)
    ctx.builder().add_incoming(result, right)
    return result


def _build_ifexp(ctx: FuncCtx, e: A.IfExp) -> Value:
    # Mirrors codegen.py's _gen_ifexp (codegen.py:11755): both arms are
    # promoted to the result type (set by sema) so the join point sees a
    # uniform register class, exactly like _build_as_float does at binop/
    # comparison sites.
    ty = e.inferred_type
    if ty.startswith("instance:") or ty in ("str", "list", "dict", "set", "tuple"):
        raise SSABuildError(f"IfExp result type {ty}: not yet wrapped")
    kind = ctx.kind_of(ty)
    body_blk, _ = new_block(ctx.func, "ifexp_body")
    else_blk, _ = new_block(ctx.func, "ifexp_else")
    merge_blk, _ = new_block(ctx.func, "ifexp_merge")
    entry_blk = ctx.block
    _build_truthy_branch(ctx, e.test, body_blk, else_blk)
    body_blk.preds.append(entry_blk)
    else_blk.preds.append(entry_blk)

    ctx.block = body_blk
    body_v = _build_as_float(ctx, e.body, A.expr_type(e.body)) if ty == "float" else build_expr(ctx, e.body)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(ctx.block)

    ctx.block = else_blk
    else_v = _build_as_float(ctx, e.orelse, A.expr_type(e.orelse)) if ty == "float" else build_expr(ctx, e.orelse)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(ctx.block)

    ctx.block = merge_blk
    result = ctx.builder().phi(kind)
    ctx.builder().add_incoming(result, body_v)
    ctx.builder().add_incoming(result, else_v)
    return result


_EXPR_BUILDERS: dict[type, Callable[[FuncCtx, object], Value]] = {
    A.IntLit: _build_intlit,
    A.FloatLit: _build_floatlit,
    A.StrLit: _build_strlit,
    A.Name: _build_name,
    A.BinOp: _build_binop,
    A.UnaryOp: _build_unaryop,
    A.Compare: _build_compare,
    A.Call: _build_call,
    A.BoolOp: _build_boolop,
    A.IfExp: _build_ifexp,
    A.ListLit: _build_listlit,
    A.Subscript: _build_subscript,
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


def _build_multiassign(ctx: FuncCtx, s: A.MultiAssign) -> None:
    # `a = b = c = value`: evaluate once, write to every target. Mirrors
    # codegen.py:2112 exactly — targets are always plain names (unlike
    # TupleAssign, which also allows Subscript/Attr targets and is
    # deferred to the container-operations wrapping pass).
    value = build_expr(ctx, s.value)
    ty = A.expr_type(s.value)
    for name in s.targets:
        if name not in ctx.locals_:
            ctx.alloc_slot(name, ty)
        _local_write(ctx, name, value)


def _build_return(ctx: FuncCtx, s: A.Return) -> None:
    value = build_expr(ctx, s.value) if s.value is not None else None
    ctx.builder().ret(value)


def _build_del(ctx: FuncCtx, s: A.Del) -> None:
    # `del x` only (codegen.py:2658) — zero the slot so the variable can't
    # be accidentally read. `del d[k]`/`del xs[i]` (codegen.py's Subscript
    # case, _runtime_dict_pop/_runtime_list_del) are deferred to the
    # container-operations wrapping pass.
    #
    # codegen.py always writes a raw integer 0 (`mov qword [...], 0`) even
    # for float slots, since all-zero bits is also +0.0 under IEEE-754 —
    # matched here rather than using fconst(0.0), which would be the same
    # bit pattern via an extra (and target-lowering-dependent) float-const
    # path for no benefit.
    if not (isinstance(s.target, A.Name) and s.target.name in ctx.locals_):
        raise SSABuildError("Del on non-local-Name target: not yet wrapped")
    _, offset = ctx.locals_[s.target.name]
    zero = ctx.builder().const(0)
    ctx.builder().store(FRAME_BASE, zero, offset=offset)


def _build_exprstmt(ctx: FuncCtx, s: A.ExprStmt) -> None:
    build_expr(ctx, s.expr)


def _build_pass(ctx: FuncCtx, s: A.Pass) -> None:
    pass


def _build_augassign(ctx: FuncCtx, s: A.AugAssign) -> None:
    # Mirrors codegen.py's AugAssign handling (codegen.py:2303) primitive
    # int/float fallback only — nonlocal boxes, dict `|=`, list `+=`,
    # str `+=`, and instance __iadd__/__add__ dunder dispatch (codegen.py:
    # 2304-2369) are all deferred (each already goes through box-pointer
    # indirection, a _runtime_* helper, or a method call today, so they're
    # CALL/RAW_ASM wrapping work, not new IR semantics).
    #
    # Reuses _build_binop wholesale rather than re-deriving the op dispatch:
    # `x += v` becomes the same IR as `x = x + v`, built via a synthetic
    # BinOp/Name pair so _build_binop's float-promotion/zero-check/floor-
    # adjustment logic (and its non-primitive-operand guard) applies
    # identically to both forms.
    ty = ctx.local_types.get(s.target)
    if ty is None:
        raise SSABuildError(f"AugAssign to {s.target!r}: not a known local (nonlocal box deferred)")
    if ty.startswith("instance:") or ty in ("str", "list", "dict", "set", "tuple"):
        raise SSABuildError(f"AugAssign {s.op!r} on {ty}: not yet wrapped")
    left = A.Name(name=s.target, inferred_type=ty)
    synthetic = A.BinOp(op=s.op, left=left, right=s.value)
    value = _build_binop(ctx, synthetic)
    _local_write(ctx, s.target, value)


def _build_truthy_branch(ctx: FuncCtx, expr: A.Expr, true_blk: ir.Block, false_blk: ir.Block) -> None:
    """Build `expr`'s truthiness and branch to `true_blk`/`false_blk`,
    mirroring codegen.py's `_gen_truthy_test` (codegen.py:11207),
    primitive int/float case only — instance __bool__/__len__ dispatch
    and container (list/dict/set/tuple) length-truthiness are deferred
    (each already goes through a method call or a runtime-helper-style
    length read today, so they're CALL/RAW_ASM wrapping work).

    Float truthiness treats NaN as truthy (NaN != 0.0 under IEEE
    semantics, matching Python) — expressed here as an explicit ICMP
    against 0.0 using FCmp's ordinary not-equal predicate. NaN compared
    with FCmp.NE against anything (including itself) is true under
    IEEE "unordered compares as not-equal for !=", so this falls out
    correctly without codegen.py's separate "jp past_nan" parity-flag
    check — that's an x86-specific lowering detail of how ucomisd's
    flags happen to encode unordered, not part of the IR's semantics
    (see docs/IR-DESIGN.md's note on FCmp sidestepping the
    ucomisd-vs-fcmp unordered-flag difference at the IR level).
    """
    t = A.expr_type(expr)
    if t.startswith("instance:") or t in ("list", "tuple", "dict", "set"):
        raise SSABuildError(f"truthiness of {t}: not yet wrapped")
    b = ctx.builder()
    if t == "float":
        v = build_expr(ctx, expr)
        b = ctx.builder()
        zero = b.fconst(0.0)
        cond = b.fcmp(Predicate.NE, v, zero)
    elif t == "str":
        # NULL-safe empty-string check (codegen.py:11251-11261): a `str |
        # None` value can be a NULL pointer (also falsy), so test for NULL
        # before dereferencing the first byte. No shared _runtime_* helper
        # exists for this (codegen.py inlines it directly with local jump
        # labels), and the IR's LOAD op has no byte-width variant to
        # express "read 1 byte" (LOAD is always a full Kind.INT/FLOAT-width
        # read) - so this is inline x86-64 text via RAW_ASM rather than
        # decomposed into typed IR ops, same rationale as the other
        # RAW_ASM sites (a real ARM64 rewrite is deferred to the ARM64
        # lowering step). Written fully branchless (no jump labels at all)
        # since RAW_ASM's target_text is static per-call-site text with no
        # mechanism (yet) to mint fresh-per-instance label names the way
        # ssa_build.py's Function.fresh_block_name does for real IR blocks
        # - a label-using version here would silently collide with itself
        # across multiple `if s:`-style checks in one function (two RAW_ASM
        # instructions emitting the literal same label text). Reading
        # byte [rax] when rax is NULL would fault, so the pointer is
        # masked to a known-safe address (rdata's own base, via `lea`)
        # before the dereference whenever it's NULL, then the *result* is
        # forced back to 0 for the NULL case via cmovz - the safe-address
        # read result is always discarded.
        v = build_expr(ctx, expr)
        b = ctx.builder()
        # Self-contained (no external call, no OS-specific register
        # convention), same text both OSes — see _X86_64_KEYS' docstring.
        strtruthy_text = "\n".join([
            "mov rcx, rax",            # rcx = original ptr (for the cmovz test)
            "test rax, rax",
            "lea rdx, [rel $]",        # rdx = a known-non-NULL safe address (this insn's own RIP)
            "cmovz rax, rdx",          # rax = NULL ? safe-addr : original ptr
            "movzx rax, byte [rax]",   # always safe to dereference now
            "test rcx, rcx",
            "cmovz rax, rcx",          # rcx is 0 here iff original was NULL -> force result to 0
        ])
        byte_or_null = b.raw_asm(
            Kind.INT, [v],
            {k: strtruthy_text for k in _X86_64_KEYS},
        )
        zero = b.const(0)
        cond = b.icmp(Predicate.NE, byte_or_null, zero)
    else:
        v = build_expr(ctx, expr)
        b = ctx.builder()
        zero = b.const(0)
        cond = b.icmp(Predicate.NE, v, zero)
    ctx.builder().condbr(cond, true_blk, false_blk)


def _build_if(ctx: FuncCtx, s: A.If) -> None:
    then_blk, then_b = new_block(ctx.func, "if_then")
    else_blk, else_b = new_block(ctx.func, "if_else")
    end_blk, end_b = new_block(ctx.func, "if_end")
    entry_blk = ctx.block
    _build_truthy_branch(ctx, s.test, then_blk, else_blk)
    then_blk.preds.append(entry_blk)
    else_blk.preds.append(entry_blk)

    ctx.block = then_blk
    build_stmts(ctx, s.then)
    if ctx.block.terminator() is None:
        ctx.builder().br(end_blk)
        end_blk.preds.append(ctx.block)
    then_exit = ctx.block

    ctx.block = else_blk
    build_stmts(ctx, s.orelse)
    if ctx.block.terminator() is None:
        ctx.builder().br(end_blk)
        end_blk.preds.append(ctx.block)

    ctx.block = end_blk
    # If `end_blk` ends up with no predecessors (both arms always
    # return/break/continue — terminate without falling through), it's
    # dead code; leave it for now since dead-block elimination is an
    # optimization pass, not a builder correctness concern, but it does
    # mean a caller appending more statements after this `if` would be
    # building into a block nothing can reach. That matches sema's
    # existing assumption that unreachable-after-return code is the
    # user's problem, not something codegen.py special-cases either.
    _ = then_exit


def _build_while(ctx: FuncCtx, s: A.While) -> None:
    top_blk, top_b = new_block(ctx.func, "while_top")
    body_blk, body_b = new_block(ctx.func, "while_body")
    end_blk, end_b = new_block(ctx.func, "while_end")
    entry_blk = ctx.block

    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    # `orelse`'s target is its own block when there's an else clause,
    # else `end_blk` directly — mirrors codegen.py:2417-2438's `nat`
    # label (the orelse entry point) only existing when an orelse
    # clause is actually present. Unlike the always-create-it first
    # version of this function, `else_blk` is only created when needed:
    # an unconditionally-created-but-sometimes-unused block violates
    # Function.validate's "every block is non-empty" invariant (caught
    # by hand-testing a plain `while` with no `else` — see the commit
    # this fix landed in for the exact failure).
    if s.orelse:
        else_blk, else_b = new_block(ctx.func, "while_else")
        cond_false_target = else_blk
    else:
        cond_false_target = end_blk
    _build_truthy_branch(ctx, s.test, body_blk, cond_false_target)
    body_blk.preds.append(top_blk)
    cond_false_target.preds.append(top_blk)

    ctx.loop_targets.append((top_blk, end_blk))
    ctx.block = body_blk
    build_stmts(ctx, s.body)
    if ctx.block.terminator() is None:
        ctx.builder().br(top_blk)
        top_blk.preds.append(ctx.block)
    ctx.loop_targets.pop()

    if s.orelse:
        ctx.block = else_blk
        build_stmts(ctx, s.orelse)
        if ctx.block.terminator() is None:
            ctx.builder().br(end_blk)
            end_blk.preds.append(ctx.block)

    ctx.block = end_blk


def _build_break(ctx: FuncCtx, s: A.Break) -> None:
    if not ctx.loop_targets:
        raise SSABuildError("break outside loop reached ssa_build (sema should reject)")
    _, break_target = ctx.loop_targets[-1]
    ctx.builder().br(break_target)
    break_target.preds.append(ctx.block)


def _build_continue(ctx: FuncCtx, s: A.Continue) -> None:
    if not ctx.loop_targets:
        raise SSABuildError("continue outside loop reached ssa_build (sema should reject)")
    continue_target, _ = ctx.loop_targets[-1]
    ctx.builder().br(continue_target)
    continue_target.preds.append(ctx.block)


def _build_for_range(ctx: FuncCtx, s: A.For) -> None:
    # Mirrors codegen.py's _gen_for (codegen.py:2753) range-args case
    # (codegen.py:2794-2866) only — `for x in <list/dict/set/str/zip/
    # enumerate/instance>` are each their own deferred wrap-as-CALL case
    # (every one of them already dispatches to a _runtime_* helper or a
    # dedicated _gen_for_* method today, none of which need new IR
    # semantics, just CALL/RAW_ASM wrapping).
    #
    # The loop direction (ascending vs. descending) is determined at
    # RUNTIME from the step's sign, not statically, since `step` can be
    # an arbitrary (non-constant) expression — `for i in range(a, b, step)`
    # with a variable `step`. This means the loop-condition check itself
    # branches on the step's sign every iteration, exactly mirroring
    # codegen.py:2832-2850's runtime `test rax, rax` / `jg pos_branch`
    # structure rather than picking one comparison direction up front.
    if s.iter is not None:
        raise SSABuildError("For (non-range iterable): not yet wrapped")
    args = s.range_args
    if len(args) == 1:
        start_e, stop_e, step_e = A.IntLit(value=0), args[0], A.IntLit(value=1)
    elif len(args) == 2:
        start_e, stop_e, step_e = args[0], args[1], A.IntLit(value=1)
    else:
        start_e, stop_e, step_e = args[0], args[1], args[2]

    start_v = build_expr(ctx, start_e)
    ctx.alloc_slot(s.var, "int")
    _local_write(ctx, s.var, start_v)
    stop_v = build_expr(ctx, stop_e)
    stop_slot_name = f"__for_stop_{id(s)}"
    ctx.alloc_slot(stop_slot_name, "int")
    _local_write(ctx, stop_slot_name, stop_v)
    step_v = build_expr(ctx, step_e)
    step_slot_name = f"__for_step_{id(s)}"
    ctx.alloc_slot(step_slot_name, "int")
    _local_write(ctx, step_slot_name, step_v)

    # Block layout (planned up front, not patched after the fact):
    #   top         -- reads step's sign, branches to pos/nonpos
    #   for_step_pos    -- step > 0: "var >= stop?" -> cond_end : body
    #   for_step_nonpos -- step <= 0: "var <= stop?" -> cond_end : body
    #   body        -- loop body, falls through to cont
    #   cont        -- var += step, jumps back to top
    #   else (optional) / end
    top_blk, top_b = new_block(ctx.func, "for_top")
    pos_blk, pos_b = new_block(ctx.func, "for_step_pos")
    nonpos_blk, nonpos_b = new_block(ctx.func, "for_step_nonpos")
    body_blk, body_b = new_block(ctx.func, "for_body")
    cont_blk, cont_b = new_block(ctx.func, "for_cont")
    end_blk, end_b = new_block(ctx.func, "for_end")
    if s.orelse:
        else_blk, else_b = new_block(ctx.func, "for_else")
        cond_end_target = else_blk
    else:
        cond_end_target = end_blk

    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    # top: branch on the step's sign (computed at runtime — see this
    # function's docstring for why it can't be decided statically).
    ctx.block = top_blk
    step_read = _local_read(ctx, step_slot_name, "int")
    b = ctx.builder()
    zero = b.const(0)
    step_positive = b.icmp(Predicate.GT, step_read, zero)
    b.condbr(step_positive, pos_blk, nonpos_blk)
    pos_blk.preds.append(top_blk)
    nonpos_blk.preds.append(top_blk)

    # step > 0: ascending: loop while var < stop (mirrors codegen.py's
    # "if var >= stop: goto cond_end").
    ctx.block = pos_blk
    var_read_pos = _local_read(ctx, s.var, "int")
    b = ctx.builder()
    stop_read_pos = _local_read(ctx, stop_slot_name, "int")
    b = ctx.builder()
    pos_done = b.icmp(Predicate.GE, var_read_pos, stop_read_pos)
    b.condbr(pos_done, cond_end_target, body_blk)
    cond_end_target.preds.append(pos_blk)
    body_blk.preds.append(pos_blk)

    # step <= 0: descending (or a no-op zero step): loop while var > stop
    # (mirrors codegen.py's "if var <= stop: goto cond_end").
    ctx.block = nonpos_blk
    var_read_nonpos = _local_read(ctx, s.var, "int")
    b = ctx.builder()
    stop_read_nonpos = _local_read(ctx, stop_slot_name, "int")
    b = ctx.builder()
    nonpos_done = b.icmp(Predicate.LE, var_read_nonpos, stop_read_nonpos)
    b.condbr(nonpos_done, cond_end_target, body_blk)
    cond_end_target.preds.append(nonpos_blk)
    body_blk.preds.append(nonpos_blk)

    ctx.loop_targets.append((cont_blk, end_blk))
    ctx.block = body_blk
    build_stmts(ctx, s.body)
    if ctx.block.terminator() is None:
        ctx.builder().br(cont_blk)
        cont_blk.preds.append(ctx.block)
    ctx.loop_targets.pop()

    ctx.block = cont_blk
    var_read_cont = _local_read(ctx, s.var, "int")
    b = ctx.builder()
    step_read_cont = _local_read(ctx, step_slot_name, "int")
    b = ctx.builder()
    next_v = b.add(var_read_cont, step_read_cont)
    _local_write(ctx, s.var, next_v)
    ctx.builder().br(top_blk)
    top_blk.preds.append(cont_blk)

    if s.orelse:
        ctx.block = else_blk
        build_stmts(ctx, s.orelse)
        if ctx.block.terminator() is None:
            ctx.builder().br(end_blk)
            end_blk.preds.append(ctx.block)

    ctx.block = end_blk


def _build_for(ctx: FuncCtx, s: A.For) -> None:
    # Mirrors codegen.py's _gen_for top-level dispatch (codegen.py:2753):
    # range-args vs. "for x in <iterable>" are different codegen paths
    # entirely. Only the range case has a real implementation here yet.
    if s.iter is None:
        _build_for_range(ctx, s)
        return
    raise SSABuildError("For (non-range iterable): not yet wrapped")


_STMT_BUILDERS: dict[type, Callable[[FuncCtx, object], None]] = {
    A.Assign: _build_assign,
    A.MultiAssign: _build_multiassign,
    A.Return: _build_return,
    A.If: _build_if,
    A.While: _build_while,
    A.For: _build_for,
    A.Break: _build_break,
    A.Continue: _build_continue,
    A.ExprStmt: _build_exprstmt,
    A.Pass: _build_pass,
    A.AugAssign: _build_augassign,
    A.Del: _build_del,
}


def build_stmt(ctx: FuncCtx, stmt: A.Stmt) -> None:
    builder = _STMT_BUILDERS.get(type(stmt))
    if builder is None:
        raise SSABuildError(f"no IR builder for statement type {type(stmt).__name__}")
    builder(ctx, stmt)


def build_stmts(ctx: FuncCtx, stmts: list) -> None:
    """Build each statement in order, stopping early if the current block
    already ends in a terminator (return/break/continue) — anything
    after that point in the same source block is unreachable, and the
    IR's one-terminator-at-the-end-only invariant (Function.validate)
    means appending more instructions to an already-terminated block is
    a structural error, not just dead code. codegen.py's direct-emission
    model doesn't need this guard (NASM has no such invariant — emitting
    unreachable instructions after a `jmp` is harmless there), so this
    is a genuine new concern the IR's stricter structure introduces, not
    a port of existing logic."""
    for s in stmts:
        if ctx.block.terminator() is not None:
            break
        build_stmt(ctx, s)
