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

# Dict/set ABI (codegen.py:8528-8549). Sets reuse the dict layout.
_DICT_CAP_OFF = 0
_DICT_LEN_OFF = 8
_DICT_TOMB_OFF = 16
_DICT_BUF_OFF = 24
_DICT_ORDER_OFF = 32
_DICT_HEADER_SIZE = 40
_DICT_SLOT_SIZE = 16


def _float_to_int_bits(ctx: FuncCtx, v: Value, hint: str) -> Value:
    """Bitcast a Kind.FLOAT SSA value to Kind.INT (same 8 bytes, different
    interpretation). Needed when passing a float to a `_runtime_*` helper that
    expects all args in integer registers (rax/rbx/... convention). Implemented
    as a round-trip through a frame slot: store float (movsd [slot], xmm0) then
    reload as int (mov rax, [slot]). Equivalent to codegen.py's `movq rax, xmm0`
    before pushing a float to stack for a helper call (codegen.py:10753-10756)."""
    slot = f"__fbits_{hint}"
    ctx.alloc_slot(slot, "float")
    _, off = ctx.locals_[slot]
    ctx.builder().store(FRAME_BASE, v, offset=off)
    return ctx.builder().load(Kind.INT, FRAME_BASE, offset=off)


def _int_to_float_bits(ctx: FuncCtx, v: Value, hint: str) -> Value:
    """Reverse bitcast: Kind.INT (raw bits from a helper's rax result) to
    Kind.FLOAT. Used when a `_runtime_*` helper returns a float's bit pattern
    in rax (e.g. list.pop on a float list). Equivalent to codegen.py's
    `movq xmm0, rax` after the helper call (codegen.py:10767-10768)."""
    slot = f"__ibits_{hint}"
    ctx.alloc_slot(slot, "int")
    _, off = ctx.locals_[slot]
    ctx.builder().store(FRAME_BASE, v, offset=off)
    return ctx.builder().load(Kind.FLOAT, FRAME_BASE, offset=off)


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


def _build_zalloc(ctx: FuncCtx, n_bytes: int) -> Value:
    # _runtime_zalloc(rbx=size) -> rax (zero-filled allocation). Internal
    # helper, not libc — same internal rax/rbx/... convention as all other
    # _runtime_* helpers; self-contained, same text on both OSes.
    return ctx.builder().raw_asm(
        Kind.INT, [],
        {k: f"mov rbx, {n_bytes}\ncall _runtime_zalloc" for k in _X86_64_KEYS},
    )


def _build_list_append_raw(ctx: FuncCtx, header: Value, raw_value: Value) -> None:
    # _runtime_list_append(rax=header, rbx=value_bits) — rbx must already
    # hold the raw 8-byte bit pattern (callers bitcast floats via
    # _float_to_int_bits first, equivalent to codegen.py's `movq rax,
    # xmm0`, codegen.py:10753-56). Shared by list.append() and list
    # comprehensions, which both need exactly this append shape.
    ctx.builder().raw_asm(
        Kind.NONE, [header, raw_value],
        {k: "call _runtime_list_append" for k in _X86_64_KEYS},
    )


def _build_alloc_dict(ctx: FuncCtx, cap: int) -> Value:
    """Allocate and fully initialize an empty dict/set header with the given
    capacity. Returns the stable header pointer (Kind.INT). Mirrors codegen.py's
    _gen_dict_lit init sequence (codegen.py:9912-9928) plus
    _emit_dict_alloc_order_buf (codegen.py:8551-8559)."""
    header = _build_malloc(ctx, _DICT_HEADER_SIZE)
    b = ctx.builder()
    b.store(header, b.const(cap), offset=_DICT_CAP_OFF)
    b = ctx.builder()
    b.store(header, b.const(0), offset=_DICT_LEN_OFF)
    b = ctx.builder()
    b.store(header, b.const(0), offset=_DICT_TOMB_OFF)
    slot_buf = _build_zalloc(ctx, cap * _DICT_SLOT_SIZE)
    b = ctx.builder()
    b.store(header, slot_buf, offset=_DICT_BUF_OFF)
    order_buf = _build_zalloc(ctx, cap * 8)
    b = ctx.builder()
    b.store(header, order_buf, offset=_DICT_ORDER_OFF)
    return header


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


def _build_comprehension(ctx: FuncCtx, e: A.Comprehension) -> Value:
    # `[elt for var in iter (if cond)]` — mirrors codegen.py's
    # _gen_comprehension (codegen.py:8591-8770) base case only: a single
    # target var (no tuple-unpack targets), a single `for` clause (no
    # `extra_for_*` nested clauses), a list/tuple iterable (not str/set/
    # instance, not the enumerate() special case), int/float elt type.
    # Real IR loop with frame-slot loop-carried state, the established
    # convention for loops in this file (_build_for_list/_build_sum), not
    # phi nodes and not RAW_ASM (RAW_ASM text can't safely contain
    # internal jump labels — this is genuine control flow, an empty
    # result list grown via repeated _runtime_list_append calls).
    if e.targets:
        raise SSABuildError("Comprehension with tuple-unpack targets: not yet wrapped")
    if e.extra_for_iters:
        raise SSABuildError("Comprehension with multiple for-clauses: not yet wrapped")
    if isinstance(e.iter, A.Call) and e.iter.func == "enumerate":
        raise SSABuildError("Comprehension over enumerate(...): not yet wrapped")
    iter_t = A.expr_type(e.iter)
    if iter_t not in ("list", "tuple"):
        raise SSABuildError(f"Comprehension over {iter_t}: not yet wrapped")
    if isinstance(e.iter, A.Name):
        src_el_t = getattr(e.iter, "list_el_type", "int")
    elif isinstance(e.iter, A.ListLit):
        src_el_t = e.iter.el_type
    else:
        src_el_t = "int"
    src_el_kind = Kind.FLOAT if src_el_t == "float" else Kind.INT
    if e.list_el_type not in ("int", "float"):
        raise SSABuildError(f"Comprehension producing {e.list_el_type} elements: not yet wrapped")

    src_header = build_expr(ctx, e.iter)
    res_header = _build_malloc_list(ctx, 0, [])  # empty list, cap=max(0,4)=4

    uid = str(id(e))
    idx_slot = f"__comp_idx_{uid}"
    len_slot = f"__comp_len_{uid}"
    buf_slot = f"__comp_buf_{uid}"
    ctx.alloc_slot(idx_slot, "int")
    ctx.alloc_slot(len_slot, "int")
    ctx.alloc_slot(buf_slot, "int")
    b = ctx.builder()
    _local_write(ctx, idx_slot, b.const(0))
    b = ctx.builder()
    src_len = b.load(Kind.INT, src_header, offset=_LIST_LEN_OFF)
    _local_write(ctx, len_slot, src_len)
    b = ctx.builder()
    src_buf = b.load(Kind.INT, src_header, offset=_LIST_BUF_OFF)
    _local_write(ctx, buf_slot, src_buf)

    top_blk, _ = new_block(ctx.func, f"comp_top_{uid}")
    body_blk, _ = new_block(ctx.func, f"comp_body_{uid}")
    end_blk, _ = new_block(ctx.func, f"comp_end_{uid}")
    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    idx_v = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    len_v = _local_read(ctx, len_slot, "int")
    b = ctx.builder()
    done = b.icmp(Predicate.GE, idx_v, len_v)
    b.condbr(done, end_blk, body_blk)
    end_blk.preds.append(top_blk)
    body_blk.preds.append(top_blk)

    ctx.block = body_blk
    idx_v2 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    buf_v = _local_read(ctx, buf_slot, "int")
    b = ctx.builder()
    byte_off = b.shl(idx_v2, b.const(3))
    b = ctx.builder()
    elem_ptr = b.add(buf_v, byte_off)
    b = ctx.builder()
    elem = b.load(src_el_kind, elem_ptr, offset=0)

    if e.var not in ctx.locals_:
        ctx.alloc_slot(e.var, src_el_t)
    _local_write(ctx, e.var, elem)

    cont_blk, _ = new_block(ctx.func, f"comp_cont_{uid}")
    if e.cond is not None:
        append_blk, _ = new_block(ctx.func, f"comp_append_{uid}")
        _build_truthy_branch(ctx, e.cond, append_blk, cont_blk)
        append_blk.preds.append(ctx.block)
        cont_blk.preds.append(ctx.block)
        ctx.block = append_blk

    elt_v = build_expr(ctx, e.elt)
    if e.list_el_type == "float":
        elt_v = _float_to_int_bits(ctx, elt_v, uid)
    _build_list_append_raw(ctx, res_header, elt_v)
    ctx.builder().br(cont_blk)
    cont_blk.preds.append(ctx.block)

    ctx.block = cont_blk
    idx_v3 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    next_idx = b.add(idx_v3, b.const(1))
    _local_write(ctx, idx_slot, next_idx)
    ctx.builder().br(top_blk)
    top_blk.preds.append(cont_blk)

    ctx.block = end_blk
    return res_header


def _build_subscript(ctx: FuncCtx, e: A.Subscript) -> Value:
    # Mirrors codegen.py's _gen_subscript (codegen.py:9253).
    # List slices and instance __getitem__ are deferred.
    if isinstance(e.index, A.Slice):
        sl: A.Slice = e.index
        if sl.step is not None:
            raise SSABuildError("Subscript with a stepped Slice: not yet wrapped")
        obj_t_sl = A.expr_type(e.obj)
        if obj_t_sl != "str":
            raise SSABuildError(f"Slice on {obj_t_sl}: not yet wrapped")
        # s[start:stop] (no step) via _runtime_str_slice(rax=s, rbx=start,
        # rcx=stop) -> new substring, codegen.py:9817-9852. Self-contained
        # internal label (negative-index/clamp logic lives inside the
        # helper), same text both OSes. Missing start defaults to 0,
        # missing stop defaults to INT64_MAX (the helper clamps both to
        # the real string length internally).
        s_v = build_expr(ctx, e.obj)
        b = ctx.builder()
        start_v = build_expr(ctx, sl.start) if sl.start is not None else b.const(0)
        b = ctx.builder()
        stop_v = build_expr(ctx, sl.stop) if sl.stop is not None else b.const(2 ** 63 - 1)
        return ctx.builder().raw_asm(
            Kind.INT, [s_v, start_v, stop_v],
            {k: "call _runtime_str_slice" for k in _X86_64_KEYS},
        )
    obj_t = A.expr_type(e.obj)

    if obj_t == "str":
        # _runtime_str_char_at(rax=s, rbx=index) handles negative indices
        # and OOB internally — no IR-level bounds check needed here.
        s_v = build_expr(ctx, e.obj)
        idx_v = build_expr(ctx, e.index)
        return ctx.builder().raw_asm(
            Kind.INT, [s_v, idx_v],
            {k: "call _runtime_str_char_at" for k in _X86_64_KEYS},
        )

    if obj_t == "dict":
        # _runtime_dict_get(rax=header, rbx=key) raises KeyError if absent.
        # Evaluate index before obj so the header ptr isn't clobbered by the
        # key expression (mirrors codegen.py's key-spill-then-obj pattern).
        key_v = build_expr(ctx, e.index)
        header_v = build_expr(ctx, e.obj)
        result = ctx.builder().raw_asm(
            Kind.INT, [header_v, key_v],
            {k: "call _runtime_dict_get" for k in _X86_64_KEYS},
        )
        if e.inferred_type == "float":
            return _int_to_float_bits(ctx, result, str(id(e)))
        return result

    if obj_t not in ("list", "tuple"):
        raise SSABuildError(f"Subscript on {obj_t}: not yet wrapped")
    el_t = e.inferred_type
    if el_t not in ("int", "float", "str"):
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


def _build_fstring_segment(ctx: FuncCtx, seg: A.Expr) -> Value:
    # One f-string segment -> a str-typed Value. Mirrors codegen.py's
    # _gen_fstring_segment (codegen.py:3869) no-format-spec, no-
    # conversion-flag fallback only (codegen.py:3941-3969) — a segment
    # carrying a `:spec` or `!r`/`!s`/`!a` conversion is deferred (each
    # routes through extra machinery: _split_fmt_align, _cfmt_for_spec,
    # alignment/precision/grouping helpers, none of which exist in this
    # IR yet). bool/None segments are also deferred (need is_bool_expr/
    # is_none_expr-driven branching to a different static string, same
    # gap str() itself has); float and instance segments need their own
    # follow-up work (float: Windows NaN/inf branching, see
    # _build_str_of_int's note; instance: __str__/__repr__ method
    # resolution).
    if getattr(seg, "fmt_spec", "") or getattr(seg, "conv_flag", ""):
        raise SSABuildError("f-string segment with a format spec or conversion: not yet wrapped")
    t = A.expr_type(seg)
    if t == "str":
        return build_expr(ctx, seg)
    if t == "int":
        if A.is_bool_expr(seg) or A.is_none_expr(seg):
            raise SSABuildError("f-string segment of bool/None: not yet wrapped")
        v = build_expr(ctx, seg)
        return _build_int_to_str(ctx, v)
    raise SSABuildError(f"f-string segment of {t}: not yet wrapped")


def _build_fstring(ctx: FuncCtx, e: A.FString) -> Value:
    # Mirrors codegen.py's _gen_fstring (codegen.py:3612-3636): convert
    # each segment to a str (via _build_fstring_segment), chain through
    # _runtime_str_concat. Empty f-string -> "".
    if not e.segments:
        return ctx.builder().string_addr("")
    acc = _build_fstring_segment(ctx, e.segments[0])
    for seg in e.segments[1:]:
        seg_v = _build_fstring_segment(ctx, seg)
        acc = _build_str_concat(ctx, acc, seg_v)
    return acc


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
        # (codegen.py's _global_label: f"__g_{name}") rather than a frame
        # offset. Op.GLOBAL_ADDR (added alongside this) carries the bare
        # name the same way Op.STRING_ADDR carries literal text — symbol
        # mangling and the actual .bss reservation are each target's
        # lowering-stage job, not ssa_build.py's. The type comes from the
        # Name node's own inferred_type (sema already stamped it), not a
        # separate FuncCtx.global_types table, since A.expr_type already
        # resolves a Name to e.inferred_type directly.
        addr = ctx.builder().global_addr(e.name)
        return ctx.builder().load(ctx.kind_of(e.inferred_type), addr, offset=0)
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


def _build_str_concat(ctx: FuncCtx, left: Value, right: Value) -> Value:
    # _runtime_str_concat(rax=a, rbx=b) -> rax (freshly allocated concat)
    # — codegen.py:2328-2332/5726-5745. Self-contained internal label (no
    # external call), same text both OSes — see _X86_64_KEYS' docstring
    # (defined near _build_str_eq). Shared by str+str BinOp and f-string
    # segment chaining, both of which need exactly this concat.
    return ctx.builder().raw_asm(
        Kind.INT, [left, right],
        {k: "call _runtime_str_concat" for k in _X86_64_KEYS},
    )


def _build_binop(ctx: FuncCtx, e: A.BinOp) -> Value:
    lt, rt = A.expr_type(e.left), A.expr_type(e.right)
    if lt == "str" and rt == "str" and e.op == "+":
        # Sema only allows str+str (no int/float coercion), so this is
        # the only string BinOp case.
        left = build_expr(ctx, e.left)
        right = build_expr(ctx, e.right)
        return _build_str_concat(ctx, left, right)
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
    if len(e.ops) == 1 and e.ops[0] in ("in", "not in"):
        # `needle in container` / `needle not in container`
        needle_expr = e.operands[0]
        container_expr = e.operands[1]
        negate = (e.ops[0] == "not in")
        ct = A.expr_type(container_expr)

        if ct in ("dict", "set"):
            # `_runtime_dict_contains(rax=header, rbx=key) -> 0/1`
            key_v = build_expr(ctx, needle_expr)
            header_v = build_expr(ctx, container_expr)
            result = ctx.builder().raw_asm(
                Kind.INT, [header_v, key_v],
                {k: "call _runtime_dict_contains" for k in _X86_64_KEYS},
            )
            if negate:
                b = ctx.builder()
                return b.xor(result, b.const(1))
            return result

        if ct in ("list", "tuple"):
            # Inline linear scan (same comparison logic as list.index but
            # returns 0/1 instead of raising). el_t from the container's
            # list_el_type attribute or element types list.
            if isinstance(container_expr, A.Name):
                el_t_in = getattr(container_expr, "list_el_type", "int")
            elif isinstance(container_expr, A.ListLit):
                el_t_in = container_expr.el_type
            elif isinstance(container_expr, A.TupleLit):
                ets_in = [t for t in container_expr.elem_types if t != "any"]
                el_t_in = ets_in[0] if ets_in else "int"
            else:
                el_t_in = "int"
            el_kind_in = Kind.FLOAT if el_t_in == "float" else Kind.INT
            needle_v = build_expr(ctx, needle_expr)
            header_v = build_expr(ctx, container_expr)
            ndl_slot = f"__listin_ndl_{id(e)}"
            idx_slot = f"__listin_idx_{id(e)}"
            len_slot = f"__listin_len_{id(e)}"
            buf_slot = f"__listin_buf_{id(e)}"
            ctx.alloc_slot(ndl_slot, el_t_in)
            ctx.alloc_slot(idx_slot, "int")
            ctx.alloc_slot(len_slot, "int")
            ctx.alloc_slot(buf_slot, "int")
            _local_write(ctx, ndl_slot, needle_v)
            b = ctx.builder()
            lst_len = b.load(Kind.INT, header_v, offset=_LIST_LEN_OFF)
            b = ctx.builder()
            lst_buf = b.load(Kind.INT, header_v, offset=_LIST_BUF_OFF)
            _local_write(ctx, len_slot, lst_len)
            _local_write(ctx, buf_slot, lst_buf)
            _local_write(ctx, idx_slot, ctx.builder().const(0))

            loop_blk, _ = new_block(ctx.func, "listin_loop")
            search_blk, _ = new_block(ctx.func, "listin_search")
            cont_blk, _ = new_block(ctx.func, "listin_cont")
            found_blk, _ = new_block(ctx.func, "listin_found")
            miss_blk, _ = new_block(ctx.func, "listin_miss")
            end_blk, _ = new_block(ctx.func, "listin_end")

            ctx.builder().br(loop_blk)
            loop_blk.preds.append(ctx.block)

            ctx.block = loop_blk
            idx_v = _local_read(ctx, idx_slot, "int")
            len_v = _local_read(ctx, len_slot, "int")
            b = ctx.builder()
            done = b.icmp(Predicate.GE, idx_v, len_v)
            b.condbr(done, miss_blk, search_blk)
            miss_blk.preds.append(loop_blk)
            search_blk.preds.append(loop_blk)

            ctx.block = search_blk
            idx_v2 = _local_read(ctx, idx_slot, "int")
            buf_v = _local_read(ctx, buf_slot, "int")
            ndl_v = _local_read(ctx, ndl_slot, el_t_in)
            b = ctx.builder()
            byte_off = b.shl(idx_v2, b.const(3))
            b = ctx.builder()
            elem_ptr = b.add(buf_v, byte_off)
            b = ctx.builder()
            elem = b.load(el_kind_in, elem_ptr, offset=0)
            if el_t_in == "str":
                eq_v = ctx.builder().raw_asm(
                    Kind.INT, [elem, ndl_v],
                    {k: "call _runtime_str_eq" for k in _X86_64_KEYS},
                )
                b = ctx.builder()
                match = b.icmp(Predicate.NE, eq_v, b.const(0))
            elif el_t_in == "float":
                match = ctx.builder().fcmp(Predicate.EQ, elem, ndl_v)
            else:
                match = ctx.builder().icmp(Predicate.EQ, elem, ndl_v)
            ctx.builder().condbr(match, found_blk, cont_blk)
            found_blk.preds.append(search_blk)
            cont_blk.preds.append(search_blk)

            ctx.block = cont_blk
            idx_v3 = _local_read(ctx, idx_slot, "int")
            b = ctx.builder()
            next_idx = b.add(idx_v3, b.const(1))
            _local_write(ctx, idx_slot, next_idx)
            ctx.builder().br(loop_blk)
            loop_blk.preds.append(cont_blk)

            res_slot = f"__listin_res_{id(e)}"
            ctx.alloc_slot(res_slot, "int")

            ctx.block = found_blk
            _local_write(ctx, res_slot, ctx.builder().const(1))
            ctx.builder().br(end_blk)
            end_blk.preds.append(found_blk)

            ctx.block = miss_blk
            _local_write(ctx, res_slot, ctx.builder().const(0))
            ctx.builder().br(end_blk)
            end_blk.preds.append(miss_blk)

            ctx.block = end_blk
            result = _local_read(ctx, res_slot, "int")
            if negate:
                b = ctx.builder()
                return b.xor(result, b.const(1))
            return result

        raise SSABuildError(f"'in'/'not in' on {ct!r}: not yet wrapped")

    if any(op in ("in", "not in") for op in e.ops):
        raise SSABuildError("Chained 'in'/'not in': not yet wrapped")
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
    # Mirrors codegen.py:11961-11981 — len()'s dispatch depends on the
    # argument's static type. list/tuple/dict/set are simple LOAD from offset
    # 8 (shared length field). instance.__len__ dispatch is deferred.
    arg = e.args[0]
    t = A.expr_type(arg)
    v = build_expr(ctx, arg)
    b = ctx.builder()
    if t == "str":
        # libc strlen(ptr) -> length. Genuinely OS-specific (Win64: rcx, SysV: rdi).
        return b.raw_asm(
            Kind.INT, [v],
            {
                "win64": "mov rcx, rax\ncall strlen",
                "linux_x86_64": "mov rdi, rax\ncall strlen",
            },
        )
    if t in ("list", "tuple"):
        return b.load(Kind.INT, v, offset=_LIST_LEN_OFF)
    if t in ("dict", "set"):
        return b.load(Kind.INT, v, offset=_DICT_LEN_OFF)
    if t.startswith("instance:"):
        raise SSABuildError(f"len(instance) __len__ dispatch: not yet wrapped")
    raise SSABuildError(f"len({t!r}): not yet wrapped")


def _build_int_to_str(ctx: FuncCtx, v: Value) -> Value:
    # Plain int Value -> fresh heap string. Shared by str(int) and
    # f-string int segments, both of which need exactly this conversion.
    # sprintf(itoa_str_buf, fmt, v) -> rax = ptr into the shared static
    # buffer (codegen.py:167-175/116-125), then _runtime_str_concat_dup
    # copies out to a fresh allocation so the result doesn't alias the
    # buffer if a caller stores it (e.g. `[str(x) for x in xs]`).
    # Genuinely OS-specific: different arg registers (Win64 rcx/rdx/r8 vs
    # SysV rdi/rsi/rdx), different format-string label name, and SysV's
    # variadic-call convention requires AL = vector-register count (0
    # here, no float args) while Win64's doesn't.
    b = ctx.builder()
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
    return _build_int_to_str(ctx, v)


def _build_int_of(ctx: FuncCtx, e: A.Call) -> Value:
    # int(x) — mirrors codegen.py:12015-12033.
    arg = e.args[0]
    arg_t = A.expr_type(arg)
    v = build_expr(ctx, arg)
    if arg_t == "str":
        return ctx.builder().raw_asm(
            Kind.INT, [v],
            {k: "call _runtime_str_to_int" for k in _X86_64_KEYS},
        )
    if arg_t == "float":
        return ctx.builder().fptosi(v)
    # int or bool: identity (bool is a subtype of int at runtime)
    return v


def _build_float_of(ctx: FuncCtx, e: A.Call) -> Value:
    # float(x) — mirrors codegen.py:12035-12062.
    arg = e.args[0]
    arg_t = A.expr_type(arg)
    # float("nan") / float("inf") / float("-inf"): direct bit-pattern constants.
    if isinstance(arg, A.StrLit):
        s = arg.value.strip().lower()
        if s == "nan":
            v = ctx.builder().const(0x7FF8000000000000)
            return _int_to_float_bits(ctx, v, f"{id(e)}_nan")
        if s in ("inf", "+inf", "infinity", "+infinity"):
            v = ctx.builder().const(0x7FF0000000000000)
            return _int_to_float_bits(ctx, v, f"{id(e)}_inf")
        if s in ("-inf", "-infinity"):
            v = ctx.builder().const(0xFFF0000000000000)
            return _int_to_float_bits(ctx, v, f"{id(e)}_ninf")
    v = build_expr(ctx, arg)
    if arg_t == "int":
        return ctx.builder().sitofp(v)
    if arg_t == "str":
        # strtod(ptr, NULL) -> result in xmm0; OS-specific call conv.
        return ctx.builder().raw_asm(
            Kind.FLOAT, [v],
            {
                "win64": "mov rcx, rax\nxor rdx, rdx\ncall strtod",
                "linux_x86_64": "mov rdi, rax\nxor rsi, rsi\ncall strtod",
            },
        )
    # float: identity
    return v


def _build_bool_of(ctx: FuncCtx, e: A.Call) -> Value:
    # bool(x) -> 0/1 truthiness — mirrors codegen.py:12547-12597.
    # Reuses _build_truthy_branch to build the CFG, then collapses to a
    # 0/1 value via a frame slot (same approach as 'in'/'not in').
    arg = e.args[0]
    arg_t = A.expr_type(arg)
    v = build_expr(ctx, arg)
    if arg_t == "float":
        b = ctx.builder()
        zero = b.fconst(0.0)
        return b.fcmp(Predicate.NE, v, zero)
    if arg_t in ("int",):
        b = ctx.builder()
        return b.icmp(Predicate.NE, v, b.const(0))
    if arg_t == "str":
        # nonzero first byte → truthy (load byte, ICMP NE 0)
        b = ctx.builder()
        ch = b.load(Kind.INT, v, offset=0)
        b = ctx.builder()
        byte_v = b.and_(ch, b.const(0xFF))
        b = ctx.builder()
        return b.icmp(Predicate.NE, byte_v, b.const(0))
    if arg_t in ("list", "tuple"):
        b = ctx.builder()
        lst_len = b.load(Kind.INT, v, offset=_LIST_LEN_OFF)
        b = ctx.builder()
        return b.icmp(Predicate.NE, lst_len, b.const(0))
    if arg_t in ("dict", "set"):
        b = ctx.builder()
        dict_len = b.load(Kind.INT, v, offset=_DICT_LEN_OFF)
        b = ctx.builder()
        return b.icmp(Predicate.NE, dict_len, b.const(0))
    raise SSABuildError(f"bool({arg_t!r}): not yet wrapped")


def _build_abs(ctx: FuncCtx, e: A.Call) -> Value:
    # abs(x) — mirrors codegen.py:12652-12675.
    arg = e.args[0]
    arg_t = A.expr_type(arg)
    v = build_expr(ctx, arg)
    if arg_t == "float":
        # Clear sign bit: movq → AND 0x7FFFFFFFFFFFFFFF → movq back.
        # RAW_ASM: args[0]→xmm0 (Kind.FLOAT input in xmm0 by convention).
        # But our convention puts args[i] in rax/rbx/rcx for INT only.
        # Float values live in xmm0 as the return value. The RAW_ASM
        # args list feeds rax/rbx/... for INT args. For a float arg we
        # must bitcast to int first, mask, then bitcast back.
        v_bits = _float_to_int_bits(ctx, v, f"{id(e)}_absbits")
        b = ctx.builder()
        masked = b.and_(v_bits, b.const(0x7FFFFFFFFFFFFFFF))
        return _int_to_float_bits(ctx, masked, f"{id(e)}_absf")
    # int: branchless `t = rax>>63; rax = (rax^t) - t`
    # RAW_ASM with no incoming values; read + write rax inline.
    return ctx.builder().raw_asm(
        Kind.INT, [v],
        {k: "mov rbx, rax\nsar rbx, 63\nxor rax, rbx\nsub rax, rbx"
         for k in _X86_64_KEYS},
    )


def _build_range_value(ctx: FuncCtx, e: A.Call) -> Value:
    # range(...) used as a VALUE (not a for-loop header — that's
    # _build_for_range, a separate dispatch in codegen.py too:
    # codegen.py:11930-11959). Materializes a real list[int] via
    # _runtime_range_list(rax=start, rbx=stop, rcx=step) -> rax, a
    # self-contained internal label (two-pass count-then-fill, no
    # external call inside it) - same text both OSes, unlike len(s)'s
    # strlen call.
    if len(e.args) == 1:
        start_v = ctx.builder().const(0)
        stop_v = build_expr(ctx, e.args[0])
        step_v = ctx.builder().const(1)
    elif len(e.args) == 2:
        start_v = build_expr(ctx, e.args[0])
        stop_v = build_expr(ctx, e.args[1])
        step_v = ctx.builder().const(1)
    else:
        start_v = build_expr(ctx, e.args[0])
        stop_v = build_expr(ctx, e.args[1])
        step_v = build_expr(ctx, e.args[2])
    return ctx.builder().raw_asm(
        Kind.INT, [start_v, stop_v, step_v],
        {k: "call _runtime_range_list" for k in _X86_64_KEYS},
    )


def _build_list_el_type(e: A.Expr) -> str:
    # Shared with _build_list_methodcall's identical inline logic — best-
    # effort static element-kind lookup for a list-typed expression: a
    # Name carries it from sema (list_el_type), a ListLit carries it
    # directly, anything else (a Call result, a Subscript, ...) falls
    # back to "int" since that information isn't tracked through those
    # expression shapes yet.
    if isinstance(e, A.Name):
        return getattr(e, "list_el_type", "int")
    if isinstance(e, A.ListLit):
        return e.el_type
    return "int"


def _build_list_whole_copy(ctx: FuncCtx, src: Value) -> Value:
    # xs[:] via _runtime_list_slice(rax=src, rbx=start, rcx=stop) -> rax,
    # using codegen.py's INT64_MIN/INT64_MAX sentinels (codegen.py:4897-
    # 4898) to mean "no explicit bound" -- the helper clamps both to the
    # real list length internally, so passing the full signed-64-bit
    # range is how codegen.py spells "the whole list" rather than reading
    # the length first. Shared by sorted()/reversed(), which must each
    # return a fresh list rather than mutating their argument in place.
    INT64_MIN = -(2 ** 63)
    INT64_MAX = 2 ** 63 - 1
    b = ctx.builder()
    start = b.const(INT64_MIN)
    stop = b.const(INT64_MAX)
    return ctx.builder().raw_asm(
        Kind.INT, [src, start, stop],
        {k: "call _runtime_list_slice" for k in _X86_64_KEYS},
    )


def _build_sorted(ctx: FuncCtx, e: A.Call) -> Value:
    # sorted(xs) — no key=/reverse= (codegen.py's full method-call sort
    # path supports both; deferred here same as list.sort() above).
    # Copies first, then sorts the COPY in place and returns it —
    # sorted() must not mutate its argument, unlike list.sort().
    if getattr(e, "kwargs", None):
        raise SSABuildError("sorted(key=...) / sorted(reverse=...): not yet wrapped")
    src = build_expr(ctx, e.args[0])
    el_t = _build_list_el_type(e.args[0])
    copy = _build_list_whole_copy(ctx, src)
    sym = "_runtime_sort_str" if el_t == "str" else "_runtime_sort_int"
    ctx.builder().raw_asm(
        Kind.NONE, [copy], {k: f"call {sym}" for k in _X86_64_KEYS},
    )
    return copy


def _build_reversed(ctx: FuncCtx, e: A.Call) -> Value:
    # reversed(xs) — copy then reverse the copy in place, leaving the
    # original untouched.
    src = build_expr(ctx, e.args[0])
    copy = _build_list_whole_copy(ctx, src)
    ctx.builder().raw_asm(
        Kind.NONE, [copy], {k: "call _runtime_list_reverse" for k in _X86_64_KEYS},
    )
    return copy


def _build_anyall(ctx: FuncCtx, e: A.Call) -> Value:
    # any(xs)/all(xs) over a list/tuple — mirrors codegen.py:12326-12357's
    # register-only scan-with-early-exit (no helper calls inside the loop;
    # codegen.py reads raw 8-byte slots and tests them for truthiness
    # directly, so this is int/pointer truthiness only — not Python's full
    # str/container truthiness rules per element, matching codegen.py
    # exactly rather than "improving" it).
    #
    # Real IR loop with frame-slot loop-carried state, same established
    # shape as _build_for_list (idx/len/header in frame slots, not phi
    # nodes — this file's convention for loops, not a RAW_ASM site, since
    # RAW_ASM text can't safely contain its own internal jump labels).
    is_all = e.func == "all"
    header = build_expr(ctx, e.args[0])
    uid = str(id(e))
    idx_slot = f"__anyall_idx_{uid}"
    len_slot = f"__anyall_len_{uid}"
    buf_slot = f"__anyall_buf_{uid}"
    ctx.alloc_slot(idx_slot, "int")
    ctx.alloc_slot(len_slot, "int")
    ctx.alloc_slot(buf_slot, "int")
    b = ctx.builder()
    _local_write(ctx, idx_slot, b.const(0))
    b = ctx.builder()
    list_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
    _local_write(ctx, len_slot, list_len)
    b = ctx.builder()
    buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
    _local_write(ctx, buf_slot, buf)

    top_blk, _ = new_block(ctx.func, f"anyall_top_{uid}")
    body_blk, _ = new_block(ctx.func, f"anyall_body_{uid}")
    hit_blk, _ = new_block(ctx.func, f"anyall_hit_{uid}")
    end_blk, _ = new_block(ctx.func, f"anyall_end_{uid}")
    merge_blk, _ = new_block(ctx.func, f"anyall_merge_{uid}")
    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    idx_v = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    len_v = _local_read(ctx, len_slot, "int")
    b = ctx.builder()
    done = b.icmp(Predicate.GE, idx_v, len_v)
    b.condbr(done, end_blk, body_blk)
    end_blk.preds.append(top_blk)
    body_blk.preds.append(top_blk)

    ctx.block = body_blk
    idx_v2 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    buf_v = _local_read(ctx, buf_slot, "int")
    b = ctx.builder()
    byte_off = b.shl(idx_v2, b.const(3))
    b = ctx.builder()
    elem_ptr = b.add(buf_v, byte_off)
    b = ctx.builder()
    elem = b.load(Kind.INT, elem_ptr, offset=0)
    b = ctx.builder()
    zero = b.const(0)
    # all(): hit (early-exit) on a FALSY element. any(): hit on a TRUTHY one.
    pred = Predicate.EQ if is_all else Predicate.NE
    is_hit = b.icmp(pred, elem, zero)
    cont_blk, _ = new_block(ctx.func, f"anyall_cont_{uid}")
    b.condbr(is_hit, hit_blk, cont_blk)
    hit_blk.preds.append(body_blk)
    cont_blk.preds.append(body_blk)

    ctx.block = cont_blk
    idx_v3 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    next_idx = b.add(idx_v3, b.const(1))
    _local_write(ctx, idx_slot, next_idx)
    ctx.builder().br(top_blk)
    top_blk.preds.append(cont_blk)

    # Ran off the end (no hit): all() -> 1 (no falsy found), any() -> 0.
    ctx.block = end_blk
    end_result = ctx.builder().const(1 if is_all else 0)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(end_blk)

    # Early exit: all() found falsy -> 0; any() found truthy -> 1.
    ctx.block = hit_blk
    hit_result = ctx.builder().const(0 if is_all else 1)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(hit_blk)

    ctx.block = merge_blk
    result = ctx.builder().phi(Kind.INT)
    ctx.builder().add_incoming(result, end_result)
    ctx.builder().add_incoming(result, hit_result)
    return result


def _build_sum(ctx: FuncCtx, e: A.Call) -> Value:
    # sum(xs[, start]): integer-only accumulation over a list/tuple buffer
    # (codegen.py:12358-12387) — reads raw 8-byte slots as ints regardless
    # of static element type, matching codegen.py exactly (no float-sum
    # special case exists there either). start is added AFTER the loop,
    # not used as the loop's initial accumulator value - codegen.py does
    # this same after-the-fact add (codegen.py:12385-12386), not because
    # it's meaningfully different but because that's what's there.
    start_v = build_expr(ctx, e.args[1]) if len(e.args) == 2 else None
    header = build_expr(ctx, e.args[0])
    uid = str(id(e))
    idx_slot = f"__sum_idx_{uid}"
    len_slot = f"__sum_len_{uid}"
    buf_slot = f"__sum_buf_{uid}"
    acc_slot = f"__sum_acc_{uid}"
    ctx.alloc_slot(idx_slot, "int")
    ctx.alloc_slot(len_slot, "int")
    ctx.alloc_slot(buf_slot, "int")
    ctx.alloc_slot(acc_slot, "int")
    b = ctx.builder()
    _local_write(ctx, idx_slot, b.const(0))
    b = ctx.builder()
    _local_write(ctx, acc_slot, b.const(0))
    b = ctx.builder()
    list_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
    _local_write(ctx, len_slot, list_len)
    b = ctx.builder()
    buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
    _local_write(ctx, buf_slot, buf)

    top_blk, _ = new_block(ctx.func, f"sum_top_{uid}")
    body_blk, _ = new_block(ctx.func, f"sum_body_{uid}")
    end_blk, _ = new_block(ctx.func, f"sum_end_{uid}")
    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    idx_v = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    len_v = _local_read(ctx, len_slot, "int")
    b = ctx.builder()
    done = b.icmp(Predicate.GE, idx_v, len_v)
    b.condbr(done, end_blk, body_blk)
    end_blk.preds.append(top_blk)
    body_blk.preds.append(top_blk)

    ctx.block = body_blk
    idx_v2 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    buf_v = _local_read(ctx, buf_slot, "int")
    b = ctx.builder()
    byte_off = b.shl(idx_v2, b.const(3))
    b = ctx.builder()
    elem_ptr = b.add(buf_v, byte_off)
    b = ctx.builder()
    elem = b.load(Kind.INT, elem_ptr, offset=0)
    b = ctx.builder()
    acc_v = _local_read(ctx, acc_slot, "int")
    b = ctx.builder()
    new_acc = b.add(acc_v, elem)
    _local_write(ctx, acc_slot, new_acc)
    idx_v3 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    next_idx = b.add(idx_v3, b.const(1))
    _local_write(ctx, idx_slot, next_idx)
    ctx.builder().br(top_blk)
    top_blk.preds.append(body_blk)

    ctx.block = end_blk
    final_acc = _local_read(ctx, acc_slot, "int")
    if start_v is not None:
        b = ctx.builder()
        return b.add(final_acc, start_v)
    return final_acc


_ISINSTANCE_PRIM_MAP = {
    "int": ("int",),
    "str": ("str",),
    "float": ("float",),
    "bool": ("int",),  # bool is a subtype of int (no separate runtime type)
    "list": ("list",),
    "dict": ("dict",),
    "tuple": ("tuple",),
    "set": ("set",),
}


def _build_isinstance(ctx: FuncCtx, e: A.Call) -> Value:
    # isinstance(x, Cls) for a single PRIMITIVE class target only (int,
    # str, float, bool, list, dict, tuple, set) — mirrors codegen.py:
    # 13257-13284 exactly, including bool mapping to runtime type "int"
    # (asmpython doesn't distinguish them at the value level). A tuple-
    # of-classes target and any instance/class-name target are deferred:
    # the latter needs _subclass_ids-style runtime class-id dispatch,
    # which FuncCtx has no tracking for yet (no class_ids field exists
    # in this IR builder's state).
    cls_arg = e.args[1]
    if not isinstance(cls_arg, A.Name) or cls_arg.name not in _ISINSTANCE_PRIM_MAP:
        raise SSABuildError(
            "isinstance() with a non-primitive or tuple class target: not yet wrapped"
        )
    arg0_t = A.expr_type(e.args[0])
    build_expr(ctx, e.args[0])  # evaluate for side effects, matching codegen.py
    match = arg0_t in _ISINSTANCE_PRIM_MAP[cls_arg.name]
    return ctx.builder().const(1 if match else 0)


def _build_hash(ctx: FuncCtx, e: A.Call) -> Value:
    # hash(x) — mirrors codegen.py:12676-12689.
    arg = e.args[0]
    arg_t = A.expr_type(arg)
    v = build_expr(ctx, arg)
    if arg_t == "str":
        return ctx.builder().raw_asm(
            Kind.INT, [v], {k: "call _runtime_hash_string" for k in _X86_64_KEYS}
        )
    # int/float/bool: identity (pointer/value is the hash)
    return v


def _build_maxmin(ctx: FuncCtx, e: A.Call) -> Value:
    # max(a, b) / min(a, b) — 2-arg scalar only (codegen.py:12388-12450).
    # Multi-arg and list-scan forms deferred.
    is_max = (e.func == "max")
    if len(e.args) != 2:
        raise SSABuildError(f"{e.func}() with {len(e.args)} args: only 2-arg form wrapped")
    a_t = A.expr_type(e.args[0])
    b_t = A.expr_type(e.args[1])
    is_float = (a_t == "float" or b_t == "float")
    a_v = _build_as_float(ctx, e.args[0], a_t) if is_float else build_expr(ctx, e.args[0])
    b_v = _build_as_float(ctx, e.args[1], b_t) if is_float else build_expr(ctx, e.args[1])
    if is_float:
        # FCMP GT → select: max(a,b) = a if a>b else b
        pred = Predicate.GT if is_max else Predicate.LT
        b = ctx.builder()
        cond = b.fcmp(pred, a_v, b_v)
        res_slot = f"__maxmin_{id(e)}"
        ctx.alloc_slot(res_slot, "float")
        true_blk, _ = new_block(ctx.func, "maxmin_true")
        false_blk, _ = new_block(ctx.func, "maxmin_false")
        merge_blk, _ = new_block(ctx.func, "maxmin_merge")
        b.condbr(cond, true_blk, false_blk)
        true_blk.preds.append(ctx.block)
        false_blk.preds.append(ctx.block)
        ctx.block = true_blk
        _local_write(ctx, res_slot, a_v)
        ctx.builder().br(merge_blk)
        merge_blk.preds.append(true_blk)
        ctx.block = false_blk
        _local_write(ctx, res_slot, b_v)
        ctx.builder().br(merge_blk)
        merge_blk.preds.append(false_blk)
        ctx.block = merge_blk
        return _local_read(ctx, res_slot, "float")
    # Int: branchless cmov — can't use a real x86 cmov in RAW_ASM because
    # the conditional depends on ICMP which is an IR value. Use a real
    # branch + frame slot (same shape as float case).
    b = ctx.builder()
    pred = Predicate.GT if is_max else Predicate.LT
    cond = b.icmp(pred, a_v, b_v)
    res_slot = f"__maxmin_{id(e)}"
    ctx.alloc_slot(res_slot, "int")
    true_blk, _ = new_block(ctx.func, "maxmin_true")
    false_blk, _ = new_block(ctx.func, "maxmin_false")
    merge_blk, _ = new_block(ctx.func, "maxmin_merge")
    b.condbr(cond, true_blk, false_blk)
    true_blk.preds.append(ctx.block)
    false_blk.preds.append(ctx.block)
    ctx.block = true_blk
    _local_write(ctx, res_slot, a_v)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(true_blk)
    ctx.block = false_blk
    _local_write(ctx, res_slot, b_v)
    ctx.builder().br(merge_blk)
    merge_blk.preds.append(false_blk)
    ctx.block = merge_blk
    return _local_read(ctx, res_slot, "int")


def _build_list_call(ctx: FuncCtx, e: A.Call) -> Value:
    # list(x) for list/tuple src: full slice via _runtime_list_slice.
    # Mirrors codegen.py:12247-12262.
    if not e.args:
        # list() with no args: empty list
        return _build_malloc_list(ctx, 0, [])
    src = build_expr(ctx, e.args[0])
    src_t = A.expr_type(e.args[0])
    if src_t not in ("list", "tuple", "any"):
        raise SSABuildError(f"list({src_t!r}): not yet wrapped")
    return ctx.builder().raw_asm(
        Kind.INT, [src],
        {k: "mov rbx, 0x8000000000000000\nmov rcx, 0x7fffffffffffffff\ncall _runtime_list_slice"
         for k in _X86_64_KEYS},
    )


def _build_malloc_list(ctx: FuncCtx, n: int, elem_exprs: list) -> Value:
    # Shared helper: build an empty list of cap=max(n,4) and populate
    # it. Used by list() with no args, and potentially list comprehensions.
    cap = max(n, 4)
    header = _build_malloc(ctx, _LIST_HEADER_SIZE)
    b = ctx.builder()
    b.store(header, b.const(cap), offset=_LIST_CAP_OFF)
    b = ctx.builder()
    b.store(header, b.const(n), offset=_LIST_LEN_OFF)
    buf = _build_malloc(ctx, cap * 8)
    b = ctx.builder()
    b.store(header, buf, offset=_LIST_BUF_OFF)
    return header


def _build_print_value(ctx: FuncCtx, arg: A.Expr, uid: str) -> None:
    # Print one scalar value, no separator/newline (codegen.py's
    # _emit_print_value, codegen.py:14003, scalar cases only — f-string
    # segments with a format spec/conversion, and containers/instances,
    # are deferred). bool/None checked before generic int, matching
    # codegen.py's is_bool_expr/is_none_expr-driven dispatch (these are
    # both statically "int"-typed but need different runtime output).
    if getattr(arg, "fmt_spec", "") or getattr(arg, "conv_flag", ""):
        raise SSABuildError("print() of an f-string segment with a format spec/conversion: not yet wrapped")
    t = A.expr_type(arg)
    if t.startswith("instance:") or t in ("list", "dict", "set", "tuple"):
        raise SSABuildError(f"print() of {t}: not yet wrapped")
    if t == "int" and A.is_bool_expr(arg):
        v = build_expr(ctx, arg)
        b = ctx.builder()
        zero = b.const(0)
        is_true = b.icmp(Predicate.NE, v, zero)
        true_blk, _ = new_block(ctx.func, f"printbool_true_{uid}")
        false_blk, _ = new_block(ctx.func, f"printbool_false_{uid}")
        merge_blk, _ = new_block(ctx.func, f"printbool_merge_{uid}")
        b.condbr(is_true, true_blk, false_blk)
        true_blk.preds.append(ctx.block)
        false_blk.preds.append(ctx.block)
        ctx.block = true_blk
        true_str = ctx.builder().string_addr("True")
        ctx.builder().br(merge_blk)
        merge_blk.preds.append(true_blk)
        ctx.block = false_blk
        false_str = ctx.builder().string_addr("False")
        ctx.builder().br(merge_blk)
        merge_blk.preds.append(false_blk)
        ctx.block = merge_blk
        s = ctx.builder().phi(Kind.INT)
        ctx.builder().add_incoming(s, true_str)
        ctx.builder().add_incoming(s, false_str)
        ctx.builder().raw_asm(
            Kind.NONE, [s],
            {k: "mov rdx, rax\nlea rcx, [fmt_str]\ncall printf" if k == "win64"
             else "mov rsi, rax\nlea rdi, [fmt_str]\nxor rax, rax\ncall printf"
             for k in _X86_64_KEYS},
        )
        return
    if t == "int" and A.is_none_expr(arg):
        build_expr(ctx, arg)  # evaluate for side effects, matching codegen.py
        s = ctx.builder().string_addr("None")
        ctx.builder().raw_asm(
            Kind.NONE, [s],
            {k: "mov rdx, rax\nlea rcx, [fmt_str]\ncall printf" if k == "win64"
             else "mov rsi, rax\nlea rdi, [fmt_str]\nxor rax, rax\ncall printf"
             for k in _X86_64_KEYS},
        )
        return
    if t == "str":
        v = build_expr(ctx, arg)
        ctx.builder().raw_asm(
            Kind.NONE, [v],
            {k: "mov rdx, rax\nlea rcx, [fmt_str]\ncall printf" if k == "win64"
             else "mov rsi, rax\nlea rdi, [fmt_str]\nxor rax, rax\ncall printf"
             for k in _X86_64_KEYS},
        )
        return
    if t == "float":
        v = build_expr(ctx, arg)
        bits = _float_to_int_bits(ctx, v, uid)
        s = ctx.builder().raw_asm(
            Kind.INT, [bits],
            {k: "mov rbx, 2\ncall _runtime_fmt_elem" for k in _X86_64_KEYS},
        )
        ctx.builder().raw_asm(
            Kind.NONE, [s],
            {k: "mov rdx, rax\nlea rcx, [fmt_str]\ncall printf" if k == "win64"
             else "mov rsi, rax\nlea rdi, [fmt_str]\nxor rax, rax\ncall printf"
             for k in _X86_64_KEYS},
        )
        return
    if t == "int":
        v = build_expr(ctx, arg)
        ctx.builder().raw_asm(
            Kind.NONE, [v],
            {k: "mov rdx, rax\nlea rcx, [fmt_int]\ncall printf" if k == "win64"
             else "mov rsi, rax\nlea rdi, [fmt_int]\nxor rax, rax\ncall printf"
             for k in _X86_64_KEYS},
        )
        return
    raise SSABuildError(f"print() of {t}: not yet wrapped")


def _build_print(ctx: FuncCtx, e: A.Call) -> Value:
    # Mirrors codegen.py's _gen_print (codegen.py:13854) for scalar args
    # only — f-string segments with a format spec/conversion and
    # container/instance args are deferred (each needs more machinery:
    # _gen_fstring_segment, _emit_container_repr). sep=/end= are honored
    # only when given as a literal A.StrLit (matching codegen.py's own
    # `isinstance(end_expr, A.StrLit)` guard — a non-literal sep/end
    # expression falls through to codegen.py's default behavior too, so
    # this isn't a new restriction).
    #
    # print() is a statement-shaped builtin (Python's print() returns
    # None) but _build_call's signature returns Value, matching every
    # other expression builder — None is represented as the int 0
    # throughout this language's value model (see list.sort()'s "xor
    # rax, rax  # returns None ~ 0", codegen.py:10796), so this returns
    # a const(0) as print()'s "value" rather than returning None from
    # this Python function (which would violate the -> Value contract
    # every _EXPR_BUILDERS entry relies on).
    kwargs = dict(getattr(e, "kwargs", None) or [])
    sep_expr = kwargs.get("sep")
    end_expr = kwargs.get("end")
    uid = str(id(e))

    def _emit_literal_str(text: str) -> None:
        if not text:
            return
        s = ctx.builder().string_addr(text)
        ctx.builder().raw_asm(
            Kind.NONE, [s],
            {k: "mov rdx, rax\nlea rcx, [fmt_str]\ncall printf" if k == "win64"
             else "mov rsi, rax\nlea rdi, [fmt_str]\nxor rax, rax\ncall printf"
             for k in _X86_64_KEYS},
        )

    if not e.args:
        if end_expr is not None and isinstance(end_expr, A.StrLit):
            _emit_literal_str(end_expr.value)
        else:
            ctx.builder().raw_asm(
                Kind.NONE, [],
                {k: "mov rcx, 10\ncall putchar" if k == "win64"
                 else "mov rdi, 10\ncall putchar"
                 for k in _X86_64_KEYS},
            )
        return ctx.builder().const(0)
    for i, arg in enumerate(e.args):
        if isinstance(arg, A.FString):
            # codegen.py:13873-13876 prints each segment individually
            # (no separator between them — they're already adjacent
            # text), rather than concatenating into one string first.
            # Matched here for the same reason: fidelity to the existing
            # behavior, plus it avoids an unnecessary heap allocation.
            for j, seg in enumerate(arg.segments):
                _build_print_value(ctx, seg, f"{uid}_{i}_{j}")
        else:
            _build_print_value(ctx, arg, f"{uid}_{i}")
        if i < len(e.args) - 1:
            if sep_expr is not None and isinstance(sep_expr, A.StrLit):
                _emit_literal_str(sep_expr.value)
            else:
                ctx.builder().raw_asm(
                    Kind.NONE, [],
                    {k: "mov rcx, 32\ncall putchar" if k == "win64"
                     else "mov rdi, 32\ncall putchar"
                     for k in _X86_64_KEYS},
                )
    if end_expr is not None and isinstance(end_expr, A.StrLit):
        if end_expr.value == "\n":
            ctx.builder().raw_asm(
                Kind.NONE, [],
                {k: "mov rcx, 10\ncall putchar" if k == "win64"
                 else "mov rdi, 10\ncall putchar"
                 for k in _X86_64_KEYS},
            )
        else:
            _emit_literal_str(end_expr.value)
    else:
        ctx.builder().raw_asm(
            Kind.NONE, [],
            {k: "mov rcx, 10\ncall putchar" if k == "win64"
             else "mov rdi, 10\ncall putchar"
             for k in _X86_64_KEYS},
        )
    return ctx.builder().const(0)


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
    if e.func == "print":
        return _build_print(ctx, e)
    if e.func == "len":
        return _build_len(ctx, e)
    if e.func == "str":
        return _build_str_of_int(ctx, e)
    if e.func == "int":
        return _build_int_of(ctx, e)
    if e.func == "float":
        return _build_float_of(ctx, e)
    if e.func == "bool":
        return _build_bool_of(ctx, e)
    if e.func == "abs":
        return _build_abs(ctx, e)
    if e.func == "chr":
        v = build_expr(ctx, e.args[0])
        return ctx.builder().raw_asm(
            Kind.INT, [v], {k: "call _runtime_chr" for k in _X86_64_KEYS}
        )
    if e.func == "ord":
        v = build_expr(ctx, e.args[0])
        return ctx.builder().raw_asm(
            Kind.INT, [v], {k: "movzx rax, byte [rax]" for k in _X86_64_KEYS}
        )
    if e.func == "id":
        return build_expr(ctx, e.args[0])
    if e.func == "hash":
        return _build_hash(ctx, e)
    if e.func == "range":
        return _build_range_value(ctx, e)
    if e.func == "sorted":
        return _build_sorted(ctx, e)
    if e.func == "reversed":
        return _build_reversed(ctx, e)
    if e.func in ("any", "all"):
        return _build_anyall(ctx, e)
    if e.func == "sum":
        return _build_sum(ctx, e)
    if e.func == "isinstance":
        return _build_isinstance(ctx, e)
    if e.func in ("max", "min"):
        return _build_maxmin(ctx, e)
    if e.func == "list":
        return _build_list_call(ctx, e)
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


# ---- str method builders -------------------------------------------------------

# Mirrors codegen.py's STR_METHOD_RUNTIME (codegen.py:10006-10043).
_STR_METHOD_SYM: dict[str, str] = {
    "upper": "_runtime_str_upper",
    "lower": "_runtime_str_lower",
    "casefold": "_runtime_str_lower",
    "capitalize": "_runtime_str_capitalize",
    "swapcase": "_runtime_str_swapcase",
    "title": "_runtime_str_title",
    "strip": "_runtime_str_strip",
    "lstrip": "_runtime_str_lstrip",
    "rstrip": "_runtime_str_rstrip",
    "zfill": "_runtime_str_zfill",
    "ljust": "_runtime_str_ljust",
    "rjust": "_runtime_str_rjust",
    "center": "_runtime_str_center",
    "startswith": "_runtime_str_starts_with",
    "endswith": "_runtime_str_ends_with",
    "removeprefix": "_runtime_str_removeprefix",
    "removesuffix": "_runtime_str_removesuffix",
    "find": "_runtime_str_index_of",
    "index": "_runtime_str_index_of",
    "rfind": "_runtime_str_rindex_of",
    "rindex": "_runtime_str_rindex_of",
    "expandtabs": "_runtime_str_expandtabs",
    "count": "_runtime_str_count",
    "replace": "_runtime_str_replace",
    "split": "_runtime_str_split",
    "splitlines": "_runtime_str_splitlines",
    "join": "_runtime_str_join",
    "partition": "_runtime_str_partition",
    "rpartition": "_runtime_str_rpartition",
    "rsplit": "_runtime_str_rsplit",
    "isdigit": "_runtime_str_isdigit",
    "isalpha": "_runtime_str_isalpha",
    "isalnum": "_runtime_str_isalnum",
    "isspace": "_runtime_str_isspace",
    "isupper": "_runtime_str_isupper",
    "islower": "_runtime_str_islower",
}


def _build_str_methodcall(ctx: FuncCtx, e: A.MethodCall) -> Value:
    # Mirrors codegen.py's _gen_str_method (codegen.py:10045-10153). Calling
    # convention: args[0]=obj->rax, args[1]=first-arg->rbx, args[2]->rcx.
    # For the arg-order mismatch (codegen.py evaluates args to stack then shuffles
    # into regs), our RAW_ASM convention already puts them in rax/rbx/rcx order
    # so no shuffling text is needed. str.format() is deferred (needs its own
    # complex literal-segment-concatenation lowering).
    if e.method == "format":
        raise SSABuildError("str.format(): not yet wrapped")
    sym = _STR_METHOD_SYM.get(e.method)
    if sym is None:
        raise SSABuildError(f"str.{e.method}(): unknown str method")
    nargs = len(e.args)
    obj = build_expr(ctx, e.obj)

    if nargs == 0:
        if e.method == "split":
            return ctx.builder().raw_asm(
                Kind.INT, [obj],
                {k: "call _runtime_str_split_ws" for k in _X86_64_KEYS},
            )
        if e.method == "expandtabs":
            return ctx.builder().raw_asm(
                Kind.INT, [obj],
                {k: "mov rbx, 8\ncall _runtime_str_expandtabs" for k in _X86_64_KEYS},
            )
        return ctx.builder().raw_asm(
            Kind.INT, [obj], {k: f"call {sym}" for k in _X86_64_KEYS},
        )

    if nargs == 1:
        arg0 = build_expr(ctx, e.args[0])
        if e.method in ("split", "rsplit"):
            # split(sep) with 1 arg: xor rcx, rcx so runtime treats rcx=0 as "no limit"
            return ctx.builder().raw_asm(
                Kind.INT, [obj, arg0],
                {k: f"xor rcx, rcx\ncall {sym}" for k in _X86_64_KEYS},
            )
        if e.method in ("ljust", "rjust", "center"):
            # ljust(width) with no fillchar: default ' ' (0x20)
            return ctx.builder().raw_asm(
                Kind.INT, [obj, arg0],
                {k: f"mov rcx, 0x20\ncall {sym}" for k in _X86_64_KEYS},
            )
        if e.method in ("find", "index"):
            # 1-arg: _runtime_str_index_of(rax=haystack, rbx=needle)
            return ctx.builder().raw_asm(
                Kind.INT, [obj, arg0],
                {k: "call _runtime_str_index_of" for k in _X86_64_KEYS},
            )
        return ctx.builder().raw_asm(
            Kind.INT, [obj, arg0], {k: f"call {sym}" for k in _X86_64_KEYS},
        )

    if nargs == 2:
        arg0 = build_expr(ctx, e.args[0])
        arg1 = build_expr(ctx, e.args[1])
        if e.method in ("ljust", "rjust", "center"):
            # (width, fillchar_str): fillchar is a str ptr in rcx (args[2]);
            # movzx extracts its first byte as the fill character (codegen.py:10109).
            return ctx.builder().raw_asm(
                Kind.INT, [obj, arg0, arg1],
                {k: f"movzx rcx, byte [rcx]\ncall {sym}" for k in _X86_64_KEYS},
            )
        if e.method in ("find", "index"):
            # 2-arg find/index(sub, start): different helper with start offset
            return ctx.builder().raw_asm(
                Kind.INT, [obj, arg0, arg1],
                {k: "call _runtime_str_index_of_start" for k in _X86_64_KEYS},
            )
        # General 2-arg: rax=obj, rbx=arg0, rcx=arg1 — covers replace, split(sep,max),
        # rsplit(sep,max), rfind(sub,start), rindex(sub,start) (codegen.py:10130-10134).
        return ctx.builder().raw_asm(
            Kind.INT, [obj, arg0, arg1],
            {k: f"call {sym}" for k in _X86_64_KEYS},
        )

    # Too many args: evaluate all for side effects, return 0 (matches
    # codegen.py:10150-10153's fallthrough).
    for a in e.args:
        build_expr(ctx, a)
    return ctx.builder().const(0)


# ---- list method builders -------------------------------------------------------


def _build_list_methodcall(ctx: FuncCtx, e: A.MethodCall) -> Value:
    # Mirrors codegen.py's _gen_method_call list branch (codegen.py:10750-10912).
    # All _runtime_list_* helpers use the internal rax/rbx/rcx convention.
    if isinstance(e.obj, A.Name):
        el_t: str = getattr(e.obj, "list_el_type", "int")
    elif isinstance(e.obj, A.ListLit):
        el_t = e.obj.el_type
    else:
        el_t = "int"

    header = build_expr(ctx, e.obj)

    if e.method == "append":
        # floats must be bitcast via _float_to_int_bits first (equivalent
        # to codegen.py's `movq rax, xmm0` + push, codegen.py:10753-56).
        arg_t = A.expr_type(e.args[0])
        val = build_expr(ctx, e.args[0])
        if arg_t == "float":
            val = _float_to_int_bits(ctx, val, str(id(e)))
        _build_list_append_raw(ctx, header, val)
        return ctx.builder().const(0)

    if e.method == "pop":
        # list.pop() (no-arg only — no index-arg pop in the runtime).
        # Returns the raw bit pattern in rax; bitcast to float if needed.
        result = ctx.builder().raw_asm(
            Kind.INT, [header],
            {k: "call _runtime_list_pop" for k in _X86_64_KEYS},
        )
        if el_t == "float" or e.inferred_type == "float":
            return _int_to_float_bits(ctx, result, str(id(e)))
        return result

    if e.method == "extend":
        src = build_expr(ctx, e.args[0])
        ctx.builder().raw_asm(
            Kind.NONE, [header, src],
            {k: "call _runtime_list_extend" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "reverse":
        ctx.builder().raw_asm(
            Kind.NONE, [header],
            {k: "call _runtime_list_reverse" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "clear":
        # Zero the length field directly — no helper call needed (codegen.py:10806).
        zero_v = ctx.builder().const(0)
        ctx.builder().store(header, zero_v, offset=_LIST_LEN_OFF)
        return ctx.builder().const(0)

    if e.method == "sort":
        sort_key = getattr(e, "sort_key", None)
        if sort_key is not None:
            raise SSABuildError("list.sort(key=...): not yet wrapped")
        sort_reverse = getattr(e, "sort_reverse", None)
        if sort_reverse is not None:
            raise SSABuildError("list.sort(reverse=...): not yet wrapped")
        sym = "_runtime_sort_str" if el_t == "str" else "_runtime_sort_int"
        ctx.builder().raw_asm(
            Kind.NONE, [header], {k: f"call {sym}" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "insert":
        # _runtime_list_insert(rax=header, rbx=idx, rcx=value)
        idx = build_expr(ctx, e.args[0])
        val = build_expr(ctx, e.args[1])
        ctx.builder().raw_asm(
            Kind.NONE, [header, idx, val],
            {k: "call _runtime_list_insert" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "copy":
        # _runtime_list_slice(rax=header, rbx=sentinel_min, rcx=sentinel_max)
        # returns a shallow copy. Sentinels baked into text (codegen.py:10819-20).
        return ctx.builder().raw_asm(
            Kind.INT, [header],
            {k: "mov rbx, 0x8000000000000000\nmov rcx, 0x7fffffffffffffff\ncall _runtime_list_slice"
             for k in _X86_64_KEYS},
        )

    if e.method == "index":
        # Linear scan returning the first matching index; raises ValueError
        # when absent. Mirrors codegen.py:10684-10742.
        # Frame slots carry needle and idx across basic-block boundaries
        # (and across _runtime_str_eq calls which clobber caller-save regs).
        # Block layout: entry → loop → search → {found | cont → loop}; loop → miss
        needle = build_expr(ctx, e.args[0])
        needle_slot = f"__lidx_needle_{id(e)}"
        idx_slot = f"__lidx_idx_{id(e)}"
        len_slot = f"__lidx_len_{id(e)}"
        buf_slot = f"__lidx_buf_{id(e)}"
        ctx.alloc_slot(needle_slot, el_t)
        ctx.alloc_slot(idx_slot, "int")
        ctx.alloc_slot(len_slot, "int")
        ctx.alloc_slot(buf_slot, "int")
        _local_write(ctx, needle_slot, needle)
        b = ctx.builder()
        lst_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
        b = ctx.builder()
        buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
        _local_write(ctx, len_slot, lst_len)
        _local_write(ctx, buf_slot, buf)
        _local_write(ctx, idx_slot, ctx.builder().const(0))

        loop_blk, _ = new_block(ctx.func, "lidx_loop")
        search_blk, _ = new_block(ctx.func, "lidx_search")
        cont_blk, _ = new_block(ctx.func, "lidx_cont")
        miss_blk, _ = new_block(ctx.func, "lidx_miss")
        found_blk, _ = new_block(ctx.func, "lidx_found")

        ctx.builder().br(loop_blk)
        loop_blk.preds.append(ctx.block)

        ctx.block = loop_blk
        idx_v = _local_read(ctx, idx_slot, "int")
        len_v = _local_read(ctx, len_slot, "int")
        b = ctx.builder()
        done = b.icmp(Predicate.GE, idx_v, len_v)
        b.condbr(done, miss_blk, search_blk)
        miss_blk.preds.append(loop_blk)
        search_blk.preds.append(loop_blk)

        ctx.block = search_blk
        idx_v2 = _local_read(ctx, idx_slot, "int")
        buf_v = _local_read(ctx, buf_slot, "int")
        needle_v = _local_read(ctx, needle_slot, el_t)
        b = ctx.builder()
        byte_off = b.shl(idx_v2, b.const(3))
        b = ctx.builder()
        elem_ptr = b.add(buf_v, byte_off)
        el_kind = Kind.FLOAT if el_t == "float" else Kind.INT
        b = ctx.builder()
        elem = b.load(el_kind, elem_ptr, offset=0)
        if el_t == "str":
            eq_v = ctx.builder().raw_asm(
                Kind.INT, [elem, needle_v],
                {k: "call _runtime_str_eq" for k in _X86_64_KEYS},
            )
            b = ctx.builder()
            match = b.icmp(Predicate.NE, eq_v, b.const(0))
        elif el_t == "float":
            match = ctx.builder().fcmp(Predicate.EQ, elem, needle_v)
        else:
            match = ctx.builder().icmp(Predicate.EQ, elem, needle_v)
        ctx.builder().condbr(match, found_blk, cont_blk)
        found_blk.preds.append(search_blk)
        cont_blk.preds.append(search_blk)

        ctx.block = cont_blk
        idx_v3 = _local_read(ctx, idx_slot, "int")
        b = ctx.builder()
        next_idx = b.add(idx_v3, b.const(1))
        _local_write(ctx, idx_slot, next_idx)
        ctx.builder().br(loop_blk)
        loop_blk.preds.append(cont_blk)

        ctx.block = miss_blk
        msg = ctx.builder().string_addr("ValueError: value not in list")
        exc_id = ctx.builder().const(BUILTIN_EXC_IDS["ValueError"])
        _build_runtime_raise(ctx, msg, exc_id)
        ctx.builder().ret(None)

        ctx.block = found_blk
        return _local_read(ctx, idx_slot, "int")

    if e.method == "count":
        # Count occurrences of value. Mirrors codegen.py:10852-10895.
        needle = build_expr(ctx, e.args[0])
        needle_slot = f"__lcnt_needle_{id(e)}"
        idx_slot = f"__lcnt_idx_{id(e)}"
        len_slot = f"__lcnt_len_{id(e)}"
        buf_slot = f"__lcnt_buf_{id(e)}"
        cnt_slot = f"__lcnt_cnt_{id(e)}"
        ctx.alloc_slot(needle_slot, el_t)
        ctx.alloc_slot(idx_slot, "int")
        ctx.alloc_slot(len_slot, "int")
        ctx.alloc_slot(buf_slot, "int")
        ctx.alloc_slot(cnt_slot, "int")
        _local_write(ctx, needle_slot, needle)
        b = ctx.builder()
        lst_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
        b = ctx.builder()
        buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
        _local_write(ctx, len_slot, lst_len)
        _local_write(ctx, buf_slot, buf)
        _local_write(ctx, idx_slot, ctx.builder().const(0))
        _local_write(ctx, cnt_slot, ctx.builder().const(0))

        loop_blk, _ = new_block(ctx.func, "lcnt_loop")
        check_blk, _ = new_block(ctx.func, "lcnt_check")
        inc_blk, _ = new_block(ctx.func, "lcnt_inc")
        cont_blk, _ = new_block(ctx.func, "lcnt_cont")
        end_blk, _ = new_block(ctx.func, "lcnt_end")

        ctx.builder().br(loop_blk)
        loop_blk.preds.append(ctx.block)

        ctx.block = loop_blk
        idx_v = _local_read(ctx, idx_slot, "int")
        len_v = _local_read(ctx, len_slot, "int")
        b = ctx.builder()
        done = b.icmp(Predicate.GE, idx_v, len_v)
        b.condbr(done, end_blk, check_blk)
        end_blk.preds.append(loop_blk)
        check_blk.preds.append(loop_blk)

        ctx.block = check_blk
        idx_v2 = _local_read(ctx, idx_slot, "int")
        buf_v = _local_read(ctx, buf_slot, "int")
        needle_v = _local_read(ctx, needle_slot, el_t)
        b = ctx.builder()
        byte_off = b.shl(idx_v2, b.const(3))
        b = ctx.builder()
        elem_ptr = b.add(buf_v, byte_off)
        el_kind = Kind.FLOAT if el_t == "float" else Kind.INT
        b = ctx.builder()
        elem = b.load(el_kind, elem_ptr, offset=0)
        if el_t == "str":
            eq_v = ctx.builder().raw_asm(
                Kind.INT, [elem, needle_v],
                {k: "call _runtime_str_eq" for k in _X86_64_KEYS},
            )
            b = ctx.builder()
            match = b.icmp(Predicate.NE, eq_v, b.const(0))
        elif el_t == "float":
            match = ctx.builder().fcmp(Predicate.EQ, elem, needle_v)
        else:
            match = ctx.builder().icmp(Predicate.EQ, elem, needle_v)
        ctx.builder().condbr(match, inc_blk, cont_blk)
        inc_blk.preds.append(check_blk)
        cont_blk.preds.append(check_blk)

        ctx.block = inc_blk
        cnt_v = _local_read(ctx, cnt_slot, "int")
        b = ctx.builder()
        new_cnt = b.add(cnt_v, b.const(1))
        _local_write(ctx, cnt_slot, new_cnt)
        ctx.builder().br(cont_blk)
        cont_blk.preds.append(ctx.block)

        ctx.block = cont_blk
        idx_v3 = _local_read(ctx, idx_slot, "int")
        b = ctx.builder()
        next_idx = b.add(idx_v3, b.const(1))
        _local_write(ctx, idx_slot, next_idx)
        ctx.builder().br(loop_blk)
        loop_blk.preds.append(ctx.block)

        ctx.block = end_blk
        return _local_read(ctx, cnt_slot, "int")

    if e.method == "remove":
        # Remove first occurrence; raises ValueError if absent.
        # Loop 1: find index (same structure as .index). Loop 2: shift
        # buf[i] = buf[i+1] for i in range(found, len-1), then decrement len.
        # Mirrors codegen.py:10911-10962.
        # Block layout: entry → find_top → {miss | fsearch → {found_shift | find_cont}}
        #   found_shift → shift_top → {shift_end | shift_copy → shift_top}
        #   shift_end → done
        el_kind = Kind.FLOAT if el_t == "float" else Kind.INT
        needle = build_expr(ctx, e.args[0])
        needle_slot = f"__lrem_needle_{id(e)}"
        idx_slot = f"__lrem_idx_{id(e)}"
        len_slot = f"__lrem_len_{id(e)}"
        buf_slot = f"__lrem_buf_{id(e)}"
        s_idx_slot = f"__lrem_sidx_{id(e)}"
        ctx.alloc_slot(needle_slot, el_t)
        ctx.alloc_slot(idx_slot, "int")
        ctx.alloc_slot(len_slot, "int")
        ctx.alloc_slot(buf_slot, "int")
        ctx.alloc_slot(s_idx_slot, "int")
        _local_write(ctx, needle_slot, needle)
        b = ctx.builder()
        lst_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
        b = ctx.builder()
        buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
        _local_write(ctx, len_slot, lst_len)
        _local_write(ctx, buf_slot, buf)
        _local_write(ctx, idx_slot, ctx.builder().const(0))

        find_top, _ = new_block(ctx.func, "lrem_find")
        fsearch, _ = new_block(ctx.func, "lrem_fsearch")
        find_cont, _ = new_block(ctx.func, "lrem_fcont")
        miss_blk, _ = new_block(ctx.func, "lrem_miss")
        shift_top, _ = new_block(ctx.func, "lrem_shift_top")
        shift_copy, _ = new_block(ctx.func, "lrem_shift_copy")
        shift_end, _ = new_block(ctx.func, "lrem_shift_end")
        done_blk, _ = new_block(ctx.func, "lrem_done")

        ctx.builder().br(find_top)
        find_top.preds.append(ctx.block)

        ctx.block = find_top
        idx_v = _local_read(ctx, idx_slot, "int")
        len_v = _local_read(ctx, len_slot, "int")
        b = ctx.builder()
        at_end = b.icmp(Predicate.GE, idx_v, len_v)
        b.condbr(at_end, miss_blk, fsearch)
        miss_blk.preds.append(find_top)
        fsearch.preds.append(find_top)

        ctx.block = fsearch
        idx_v2 = _local_read(ctx, idx_slot, "int")
        buf_v = _local_read(ctx, buf_slot, "int")
        needle_v = _local_read(ctx, needle_slot, el_t)
        b = ctx.builder()
        byte_off = b.shl(idx_v2, b.const(3))
        b = ctx.builder()
        elem_ptr = b.add(buf_v, byte_off)
        b = ctx.builder()
        elem = b.load(el_kind, elem_ptr, offset=0)
        if el_t == "str":
            eq_v = ctx.builder().raw_asm(
                Kind.INT, [elem, needle_v],
                {k: "call _runtime_str_eq" for k in _X86_64_KEYS},
            )
            b = ctx.builder()
            match = b.icmp(Predicate.NE, eq_v, b.const(0))
        elif el_t == "float":
            match = ctx.builder().fcmp(Predicate.EQ, elem, needle_v)
        else:
            match = ctx.builder().icmp(Predicate.EQ, elem, needle_v)
        ctx.builder().condbr(match, shift_top, find_cont)
        shift_top.preds.append(fsearch)
        find_cont.preds.append(fsearch)

        ctx.block = find_cont
        idx_v3 = _local_read(ctx, idx_slot, "int")
        b = ctx.builder()
        next_idx = b.add(idx_v3, b.const(1))
        _local_write(ctx, idx_slot, next_idx)
        ctx.builder().br(find_top)
        find_top.preds.append(find_cont)

        ctx.block = miss_blk
        msg = ctx.builder().string_addr("ValueError: value not in list")
        exc_id = ctx.builder().const(BUILTIN_EXC_IDS["ValueError"])
        _build_runtime_raise(ctx, msg, exc_id)
        ctx.builder().ret(None)

        # --- shift loop ---
        # shift_top is the init block (one-shot): set up s_idx and stop,
        # then fall into shift_loop (the real header).
        # Layout: shift_top(init) → shift_loop → {shift_end | shift_body → shift_loop}
        shift_loop, _ = new_block(ctx.func, "lrem_shift_loop")
        shift_body2, _ = new_block(ctx.func, "lrem_shift_body")

        ctx.block = shift_top
        found_idx = _local_read(ctx, idx_slot, "int")
        len_v2 = _local_read(ctx, len_slot, "int")
        b = ctx.builder()
        stop_val = b.sub(len_v2, b.const(1))
        s_stop_slot = f"__lrem_stop_{id(e)}"
        ctx.alloc_slot(s_stop_slot, "int")
        _local_write(ctx, s_idx_slot, found_idx)
        _local_write(ctx, s_stop_slot, stop_val)
        ctx.builder().br(shift_loop)
        shift_loop.preds.append(shift_top)

        ctx.block = shift_loop
        s_idx = _local_read(ctx, s_idx_slot, "int")
        stop_v2 = _local_read(ctx, s_stop_slot, "int")
        b = ctx.builder()
        at_stop = b.icmp(Predicate.GE, s_idx, stop_v2)
        b.condbr(at_stop, shift_end, shift_body2)
        shift_end.preds.append(shift_loop)
        shift_body2.preds.append(shift_loop)

        ctx.block = shift_body2
        s_idx2 = _local_read(ctx, s_idx_slot, "int")
        buf_v2 = _local_read(ctx, buf_slot, "int")
        b = ctx.builder()
        next_off = b.add(s_idx2, b.const(1))
        b = ctx.builder()
        src_byte = b.shl(next_off, b.const(3))
        b = ctx.builder()
        src_ptr = b.add(buf_v2, src_byte)
        b = ctx.builder()
        val_to_copy = b.load(el_kind, src_ptr, offset=0)
        b = ctx.builder()
        dst_byte = b.shl(s_idx2, b.const(3))
        b = ctx.builder()
        dst_ptr = b.add(buf_v2, dst_byte)
        ctx.builder().store(dst_ptr, val_to_copy, offset=0)
        s_idx3 = _local_read(ctx, s_idx_slot, "int")
        b = ctx.builder()
        next_s = b.add(s_idx3, b.const(1))
        _local_write(ctx, s_idx_slot, next_s)
        ctx.builder().br(shift_loop)
        shift_loop.preds.append(shift_body2)

        ctx.block = shift_end
        len_v3 = _local_read(ctx, len_slot, "int")
        b = ctx.builder()
        new_len = b.sub(len_v3, b.const(1))
        ctx.builder().store(header, new_len, offset=_LIST_LEN_OFF)
        ctx.builder().br(done_blk)
        done_blk.preds.append(shift_end)

        ctx.block = done_blk
        return ctx.builder().const(0)

    raise SSABuildError(f"list.{e.method}(): not yet wrapped")


# ---- dict / set / tuple builders -----------------------------------------------


def _build_dictlit(ctx: FuncCtx, e: A.DictLit) -> Value:
    # Mirrors codegen.py's _gen_dict_lit (codegen.py:9902-9958).
    # `**spread` entries: key is None in e.keys (PEP 448).
    n = len(e.keys)
    cap = 8
    while cap < n * 2:
        cap *= 2
    header = _build_alloc_dict(ctx, cap)
    for k_expr, v_expr in zip(e.keys, e.values):
        if k_expr is None:
            # `**other`: merge other dict into this one in source order
            other = build_expr(ctx, v_expr)
            ctx.builder().raw_asm(
                Kind.NONE, [header, other],
                {k: "call _runtime_dict_update" for k in _X86_64_KEYS},
            )
            continue
        key_v = build_expr(ctx, k_expr)
        val_v = build_expr(ctx, v_expr)
        val_t = A.expr_type(v_expr)
        if val_t == "float":
            # dict values are stored as raw 8-byte bit patterns (codegen.py:9951)
            val_v = _float_to_int_bits(ctx, val_v, f"{id(v_expr)}")
        # _runtime_dict_set(rax=header, rbx=key_ptr, rcx=value_bits)
        ctx.builder().raw_asm(
            Kind.NONE, [header, key_v, val_v],
            {k: "call _runtime_dict_set" for k in _X86_64_KEYS},
        )
    return header


def _build_setlit(ctx: FuncCtx, e: A.SetLit) -> Value:
    # Sets are dicts keyed by their members with a dummy value of 1
    # (codegen.py:9960-10002). Only str elements are supported here
    # (int elements need _emit_int_to_str, which is str(int) lowering —
    # deferred until we have a shared int-to-str IR helper factored out).
    n = len(e.elems)
    cap = 8
    while cap < n * 2:
        cap *= 2
    header = _build_alloc_dict(ctx, cap)
    for el_expr in e.elems:
        el_t = A.expr_type(el_expr)
        if el_t != "str":
            raise SSABuildError(f"SetLit with {el_t!r} element: only str elements wrapped so far")
        key_v = build_expr(ctx, el_expr)
        # _runtime_dict_set(rax=header, rbx=key_ptr, rcx=1)
        ctx.builder().raw_asm(
            Kind.NONE, [header, key_v],
            {k: "mov rcx, 1\ncall _runtime_dict_set" for k in _X86_64_KEYS},
        )
    return header


def _build_tuplelit(ctx: FuncCtx, e: A.TupleLit) -> Value:
    # Tuples reuse the list layout (codegen.py:875-892 and _gen_list_lit pattern).
    # Unlike ListLit, elements may be heterogeneous: each elem_types[i] is its own type.
    n = len(e.elems)
    cap = max(n, 4)
    header = _build_malloc(ctx, _LIST_HEADER_SIZE)
    b = ctx.builder()
    b.store(header, b.const(cap), offset=_LIST_CAP_OFF)
    b = ctx.builder()
    b.store(header, b.const(n), offset=_LIST_LEN_OFF)
    buf = _build_malloc(ctx, cap * 8)
    b = ctx.builder()
    b.store(header, buf, offset=_LIST_BUF_OFF)
    for i, (el_expr, el_ty) in enumerate(zip(e.elems, e.elem_types)):
        el_v = build_expr(ctx, el_expr)
        b = ctx.builder()
        b.store(buf, el_v, offset=i * 8)
    return header


def _build_dict_methodcall(ctx: FuncCtx, e: A.MethodCall) -> Value:
    # Mirrors codegen.py's _gen_method_call dict branch (codegen.py:10423-10572).
    # dict.copy() and dict.setdefault() need block diamonds / second alloc; deferred.
    header = build_expr(ctx, e.obj)

    if e.method == "get":
        key = build_expr(ctx, e.args[0])
        if len(e.args) >= 2:
            default = build_expr(ctx, e.args[1])
            if A.expr_type(e.args[1]) == "float":
                default = _float_to_int_bits(ctx, default, f"{id(e)}_def")
        else:
            default = ctx.builder().const(0)
        result = ctx.builder().raw_asm(
            Kind.INT, [header, key, default],
            {k: "call _runtime_dict_get_default" for k in _X86_64_KEYS},
        )
        if e.inferred_type == "float":
            return _int_to_float_bits(ctx, result, str(id(e)))
        return result

    if e.method == "keys":
        return ctx.builder().raw_asm(
            Kind.INT, [header],
            {k: "call _runtime_dict_keys" for k in _X86_64_KEYS},
        )

    if e.method == "values":
        return ctx.builder().raw_asm(
            Kind.INT, [header],
            {k: "call _runtime_dict_values" for k in _X86_64_KEYS},
        )

    if e.method == "items":
        return ctx.builder().raw_asm(
            Kind.INT, [header],
            {k: "call _runtime_dict_items" for k in _X86_64_KEYS},
        )

    if e.method == "update":
        src = build_expr(ctx, e.args[0])
        ctx.builder().raw_asm(
            Kind.NONE, [header, src],
            {k: "call _runtime_dict_update" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "pop":
        key = build_expr(ctx, e.args[0])
        if len(e.args) >= 2:
            raise SSABuildError("dict.pop(key, default): not yet wrapped (needs branching)")
        result = ctx.builder().raw_asm(
            Kind.INT, [header, key],
            {k: "call _runtime_dict_pop" for k in _X86_64_KEYS},
        )
        if e.inferred_type == "float":
            return _int_to_float_bits(ctx, result, str(id(e)))
        return result

    if e.method == "contains":
        key = build_expr(ctx, e.args[0])
        return ctx.builder().raw_asm(
            Kind.INT, [header, key],
            {k: "call _runtime_dict_contains" for k in _X86_64_KEYS},
        )

    if e.method == "clear":
        ctx.builder().raw_asm(
            Kind.NONE, [header], {k: "call _runtime_dict_clear" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method in ("copy", "setdefault"):
        raise SSABuildError(f"dict.{e.method}(): not yet wrapped (needs alloc + block diamond)")

    raise SSABuildError(f"dict.{e.method}(): not yet wrapped")


def _build_set_methodcall(ctx: FuncCtx, e: A.MethodCall) -> Value:
    # Sets are dicts keyed by str members (codegen.py:10573-10683).
    # set.discard is implemented with real IR control flow (contains + conditional pop).
    header = build_expr(ctx, e.obj)

    if e.method == "add":
        el_t = A.expr_type(e.args[0])
        if el_t != "str":
            raise SSABuildError(f"set.add({el_t!r}): only str members wrapped so far")
        key = build_expr(ctx, e.args[0])
        ctx.builder().raw_asm(
            Kind.NONE, [header, key],
            {k: "mov rcx, 1\ncall _runtime_dict_set" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "clear":
        ctx.builder().raw_asm(
            Kind.NONE, [header], {k: "call _runtime_dict_clear" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method == "update":
        src = build_expr(ctx, e.args[0])
        ctx.builder().raw_asm(
            Kind.NONE, [header, src],
            {k: "call _runtime_dict_update" for k in _X86_64_KEYS},
        )
        return ctx.builder().const(0)

    if e.method in ("discard", "remove"):
        el_t = A.expr_type(e.args[0])
        if el_t != "str":
            raise SSABuildError(f"set.{e.method}({el_t!r}): only str members wrapped so far")
        key = build_expr(ctx, e.args[0])
        if e.method == "remove":
            # remove: unconditional pop (KeyError if absent)
            ctx.builder().raw_asm(
                Kind.NONE, [header, key],
                {k: "call _runtime_dict_pop" for k in _X86_64_KEYS},
            )
            return ctx.builder().const(0)
        # discard: only pop if present — real IR control flow (CONDBR)
        in_set = ctx.builder().raw_asm(
            Kind.INT, [header, key],
            {k: "call _runtime_dict_contains" for k in _X86_64_KEYS},
        )
        b = ctx.builder()
        present = b.icmp(Predicate.NE, in_set, b.const(0))
        pop_blk, _ = new_block(ctx.func, "sdiscard_pop")
        end_blk, _ = new_block(ctx.func, "sdiscard_end")
        b.condbr(present, pop_blk, end_blk)
        pop_blk.preds.append(ctx.block)
        end_blk.preds.append(ctx.block)
        ctx.block = pop_blk
        ctx.builder().raw_asm(
            Kind.NONE, [header, key],
            {k: "call _runtime_dict_pop" for k in _X86_64_KEYS},
        )
        ctx.builder().br(end_blk)
        end_blk.preds.append(ctx.block)
        ctx.block = end_blk
        return ctx.builder().const(0)

    if e.method in ("union", "intersection", "difference", "pop", "copy"):
        raise SSABuildError(f"set.{e.method}(): not yet wrapped")

    raise SSABuildError(f"set.{e.method}(): not yet wrapped")


def _build_methodcall(ctx: FuncCtx, e: A.MethodCall) -> Value:
    # Top-level dispatch mirroring codegen.py's _gen_method_call
    # (codegen.py:10212): route by the receiver's static type.
    # Module-qualified calls, super(), user class methods, and virtual
    # dispatch are all deferred (each needs class resolution machinery
    # not yet threaded into FuncCtx).
    obj_t = A.expr_type(e.obj)
    if obj_t == "str":
        return _build_str_methodcall(ctx, e)
    if obj_t in ("list", "tuple"):
        return _build_list_methodcall(ctx, e)
    if obj_t == "dict":
        return _build_dict_methodcall(ctx, e)
    if obj_t == "set":
        return _build_set_methodcall(ctx, e)
    if obj_t.startswith("instance:"):
        raise SSABuildError(f"MethodCall on instance ({obj_t}): not yet wrapped")
    raise SSABuildError(f"MethodCall on {obj_t!r}: not yet wrapped")


def _build_namedexpr(ctx: FuncCtx, e: A.NamedExpr) -> Value:
    # `target := value` — evaluate value, assign to target (like Assign),
    # and return the value so the enclosing expression can use it.
    # Mirrors codegen.py's _gen_named_expr (codegen.py:3582).
    value = build_expr(ctx, e.value)
    ty = A.expr_type(e.value)
    if e.target not in ctx.locals_:
        ctx.alloc_slot(e.target, ty)
    _local_write(ctx, e.target, value)
    return value


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
    A.DictLit: _build_dictlit,
    A.SetLit: _build_setlit,
    A.TupleLit: _build_tuplelit,
    A.Subscript: _build_subscript,
    A.MethodCall: _build_methodcall,
    A.NamedExpr: _build_namedexpr,
    A.Comprehension: _build_comprehension,
    A.FString: _build_fstring,
}


def build_expr(ctx: FuncCtx, expr: A.Expr) -> Value:
    builder = _EXPR_BUILDERS.get(type(expr))
    if builder is None:
        raise SSABuildError(f"no IR builder for expression type {type(expr).__name__}")
    return builder(ctx, expr)


# ---- statement builders -------------------------------------------------------


def _build_assign(ctx: FuncCtx, s: A.Assign) -> None:
    value = build_expr(ctx, s.value)
    if s.target not in ctx.locals_ and s.target in ctx.global_names:
        # `global x; x = value` — write through the .bss slot, never
        # shadow with a fresh local. This branch existing at all is a
        # real bug fix: the unconditional alloc_slot below would have
        # silently created a same-named LOCAL instead of writing the
        # actual global, for any name declared `global` but not already
        # present in ctx.locals_ (which a global, by definition, never is).
        addr = ctx.builder().global_addr(s.target)
        ctx.builder().store(addr, value, offset=0)
        return
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


def _build_indexassign(ctx: FuncCtx, s: A.IndexAssign) -> None:
    # Write-side counterpart to _build_subscript, mirroring codegen.py's
    # IndexAssign cases (codegen.py:2453-2608). Slice-target and __setitem__
    # dispatch are deferred; list, tuple, and dict targets are all handled.
    if isinstance(s.target.index, A.Slice):
        raise SSABuildError("IndexAssign with a Slice target: not yet wrapped")
    obj_t = A.expr_type(s.target.obj)

    if obj_t == "dict":
        # _runtime_dict_set(rax=header, rbx=key, rcx=value_bits)
        key_v = build_expr(ctx, s.target.index)
        header_v = build_expr(ctx, s.target.obj)
        val_v = build_expr(ctx, s.value)
        val_t = A.expr_type(s.value)
        if val_t == "float":
            val_v = _float_to_int_bits(ctx, val_v, str(id(s)))
        ctx.builder().raw_asm(
            Kind.NONE, [header_v, key_v, val_v],
            {k: "call _runtime_dict_set" for k in _X86_64_KEYS},
        )
        return

    if obj_t not in ("list", "tuple"):
        raise SSABuildError(f"IndexAssign on {obj_t}: not yet wrapped")

    # List/tuple write: negative-index wraparound, no bounds check (matches
    # codegen.py:2526-2546's silent-corrupt-on-oob behaviour exactly).
    header = build_expr(ctx, s.target.obj)
    raw_idx = build_expr(ctx, s.target.index)
    value = build_expr(ctx, s.value)
    _build_list_subscript_store(ctx, header, raw_idx, value)


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


def _build_global(ctx: FuncCtx, s: A.Global) -> None:
    # No-op at build time, matching codegen.py exactly (codegen.py:2107-
    # 2108: "return # handled at _cl_walk time"). The actual EFFECT of
    # `global x` (routing later Name reads/writes of x to the .bss slot
    # instead of a frame slot) comes entirely from ctx.global_names
    # already containing the name — populated by the caller before this
    # function's body is ever built (see FuncCtx.global_names' docstring),
    # not incrementally as Global statements are walked. So encountering
    # this statement mid-body changes nothing that isn't already in
    # effect for every Name reference to this name in this function.
    pass


def _build_nonlocal(ctx: FuncCtx, s: A.Nonlocal) -> None:
    # No-op, matching codegen.py exactly (codegen.py:2656-2657: "closures
    # not supported; accept as no-op"). Closures/nonlocal-box machinery
    # isn't implemented in this IR yet either, so this is genuinely the
    # same scope limitation as the existing direct-emission path, not a
    # new restriction introduced here.
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
    if ty == "dict" and s.op == "|":
        # `d |= other` (PEP 584): merge other's entries into d in-place.
        # The header pointer itself doesn't change — update through it.
        other = build_expr(ctx, s.value)
        header = _local_read(ctx, s.target, "int")
        ctx.builder().raw_asm(
            Kind.NONE, [header, other],
            {k: "call _runtime_dict_update" for k in _X86_64_KEYS},
        )
        return
    if ty == "list" and s.op == "+":
        # `xs += other` → extend xs in-place (same as xs.extend(other)).
        other = build_expr(ctx, s.value)
        header = _local_read(ctx, s.target, "int")
        ctx.builder().raw_asm(
            Kind.NONE, [header, other],
            {k: "call _runtime_list_extend" for k in _X86_64_KEYS},
        )
        return
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
    if t.startswith("instance:"):
        raise SSABuildError(f"truthiness of {t}: not yet wrapped")
    b = ctx.builder()
    if t in ("list", "tuple"):
        # Truthy when len > 0: load [header+LIST_LEN_OFF] and compare != 0.
        # Mirrors codegen.py:11225-11231's `mov rax, [rax+LIST_LEN_OFF]; test`.
        v = build_expr(ctx, expr)
        b = ctx.builder()
        length = b.load(Kind.INT, v, offset=_LIST_LEN_OFF)
        b = ctx.builder()
        zero = b.const(0)
        cond = b.icmp(Predicate.NE, length, zero)
        ctx.builder().condbr(cond, true_blk, false_blk)
        return
    if t in ("dict", "set"):
        v = build_expr(ctx, expr)
        b = ctx.builder()
        length = b.load(Kind.INT, v, offset=_DICT_LEN_OFF)
        b = ctx.builder()
        zero = b.const(0)
        cond = b.icmp(Predicate.NE, length, zero)
        ctx.builder().condbr(cond, true_blk, false_blk)
        return
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


def _build_for_list(ctx: FuncCtx, s: A.For) -> None:
    # `for x in xs:` where xs is a list or tuple. Mirrors codegen.py's
    # _gen_for_list (codegen.py:3171-3235): index counter 0..len(xs)-1,
    # loading buf[i*8] each iteration (buffer reloaded each time in case an
    # append inside the body reallocates it — codegen.py:3201-3207). Only the
    # single-variable case: tuple-unpack targets (`for a, b in pairs:`) are
    # deferred (need per-slot reads from a per-iteration inner list).
    if s.targets:
        raise SSABuildError("For (tuple-unpack targets) over list/tuple: not yet wrapped")

    # Determine element kind from the iterable's annotation.
    if isinstance(s.iter, A.Name):
        el_t = getattr(s.iter, "list_el_type", "int")
    elif isinstance(s.iter, A.ListLit):
        el_t = s.iter.el_type
    else:
        el_t = "int"
    el_kind = Kind.FLOAT if el_t == "float" else Kind.INT

    header = build_expr(ctx, s.iter)

    # Allocate frame slots for loop-carried state (index, cached length,
    # header pointer) — the same reason _build_for_range uses frame slots:
    # these values span basic-block boundaries and phi nodes would be the
    # "correct" SSA approach but frame slots are the established pattern here.
    idx_slot = f"__forlist_idx_{id(s)}"
    len_slot = f"__forlist_len_{id(s)}"
    hdr_slot = f"__forlist_hdr_{id(s)}"
    ctx.alloc_slot(idx_slot, "int")
    ctx.alloc_slot(len_slot, "int")
    ctx.alloc_slot(hdr_slot, "int")

    _local_write(ctx, idx_slot, ctx.builder().const(0))
    b = ctx.builder()
    initial_len = b.load(Kind.INT, header, offset=_LIST_LEN_OFF)
    _local_write(ctx, len_slot, initial_len)
    _local_write(ctx, hdr_slot, header)

    top_blk, _ = new_block(ctx.func, "forlist_top")
    body_blk, _ = new_block(ctx.func, "forlist_body")
    cont_blk, _ = new_block(ctx.func, "forlist_cont")
    end_blk, _ = new_block(ctx.func, "forlist_end")
    if s.orelse:
        else_blk, _ = new_block(ctx.func, "forlist_else")
        exit_target = else_blk
    else:
        exit_target = end_blk

    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    idx_v = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    len_v = _local_read(ctx, len_slot, "int")
    b = ctx.builder()
    done = b.icmp(Predicate.GE, idx_v, len_v)
    b.condbr(done, exit_target, body_blk)
    exit_target.preds.append(top_blk)
    body_blk.preds.append(top_blk)

    ctx.block = body_blk
    # Reload buf each iteration: a list.append() inside the body may
    # realloc the buffer (same reason as codegen.py:3201-3207).
    hdr_v = _local_read(ctx, hdr_slot, "int")
    b = ctx.builder()
    buf = b.load(Kind.INT, hdr_v, offset=_LIST_BUF_OFF)
    b = ctx.builder()
    idx_v2 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    byte_off = b.shl(idx_v2, b.const(3))
    b = ctx.builder()
    elem_ptr = b.add(buf, byte_off)
    b = ctx.builder()
    elem = b.load(el_kind, elem_ptr, offset=0)

    if s.var not in ctx.locals_:
        ctx.alloc_slot(s.var, el_t)
    _local_write(ctx, s.var, elem)

    ctx.loop_targets.append((cont_blk, end_blk))
    build_stmts(ctx, s.body)
    if ctx.block.terminator() is None:
        ctx.builder().br(cont_blk)
        cont_blk.preds.append(ctx.block)
    ctx.loop_targets.pop()

    ctx.block = cont_blk
    idx_v3 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    next_idx = b.add(idx_v3, b.const(1))
    _local_write(ctx, idx_slot, next_idx)
    ctx.builder().br(top_blk)
    top_blk.preds.append(cont_blk)

    if s.orelse:
        ctx.block = else_blk
        build_stmts(ctx, s.orelse)
        if ctx.block.terminator() is None:
            ctx.builder().br(end_blk)
            end_blk.preds.append(ctx.block)

    ctx.block = end_blk


def _build_for_str(ctx: FuncCtx, s: A.For) -> None:
    # `for c in some_str:` — mirrors codegen.py's _gen_for_str (codegen.py:3334).
    # Each iteration calls _runtime_str_char_at(rax=ptr, rbx=index) which
    # allocates a fresh 1-char string. Length is cached via strlen at loop entry.
    str_v = build_expr(ctx, s.iter)

    str_slot = f"__forstr_ptr_{id(s)}"
    len_slot = f"__forstr_len_{id(s)}"
    idx_slot = f"__forstr_idx_{id(s)}"
    ctx.alloc_slot(str_slot, "int")
    ctx.alloc_slot(len_slot, "int")
    ctx.alloc_slot(idx_slot, "int")

    _local_write(ctx, str_slot, str_v)
    # strlen(rax=ptr) -> rax=len; Win64 uses rcx, SysV uses rdi.
    str_len = ctx.builder().raw_asm(
        Kind.INT, [str_v],
        {"win64": "mov rcx, rax\ncall strlen", "linux_x86_64": "mov rdi, rax\ncall strlen"},
    )
    _local_write(ctx, len_slot, str_len)
    _local_write(ctx, idx_slot, ctx.builder().const(0))

    top_blk, _ = new_block(ctx.func, "forstr_top")
    body_blk, _ = new_block(ctx.func, "forstr_body")
    cont_blk, _ = new_block(ctx.func, "forstr_cont")
    end_blk, _ = new_block(ctx.func, "forstr_end")
    if s.orelse:
        else_blk, _ = new_block(ctx.func, "forstr_else")
        exit_target = else_blk
    else:
        exit_target = end_blk

    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    idx_v = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    len_v = _local_read(ctx, len_slot, "int")
    b = ctx.builder()
    done = b.icmp(Predicate.GE, idx_v, len_v)
    b.condbr(done, exit_target, body_blk)
    exit_target.preds.append(top_blk)
    body_blk.preds.append(top_blk)

    ctx.block = body_blk
    str_v2 = _local_read(ctx, str_slot, "int")
    idx_v2 = _local_read(ctx, idx_slot, "int")
    char_v = ctx.builder().raw_asm(
        Kind.INT, [str_v2, idx_v2],
        {k: "call _runtime_str_char_at" for k in _X86_64_KEYS},
    )

    if s.var not in ctx.locals_:
        ctx.alloc_slot(s.var, "str")
    _local_write(ctx, s.var, char_v)

    ctx.loop_targets.append((cont_blk, end_blk))
    build_stmts(ctx, s.body)
    if ctx.block.terminator() is None:
        ctx.builder().br(cont_blk)
        cont_blk.preds.append(ctx.block)
    ctx.loop_targets.pop()

    ctx.block = cont_blk
    idx_v3 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    next_idx = b.add(idx_v3, b.const(1))
    _local_write(ctx, idx_slot, next_idx)
    ctx.builder().br(top_blk)
    top_blk.preds.append(cont_blk)

    if s.orelse:
        ctx.block = else_blk
        build_stmts(ctx, s.orelse)
        if ctx.block.terminator() is None:
            ctx.builder().br(end_blk)
            end_blk.preds.append(ctx.block)

    ctx.block = end_blk


def _build_for_dict(ctx: FuncCtx, s: A.For) -> None:
    # `for k in d:` — walks order_buf[0..len) directly (codegen.py:2990).
    # Insertion order is preserved (CPython 3.7+ semantics); order_buf has
    # no tombstone gaps so a simple 0..len counter suffices.
    # The iteration variable receives str keys only (dict keys are always str).
    if s.targets:
        raise SSABuildError("For (tuple-unpack targets) over dict: not yet wrapped")

    header = build_expr(ctx, s.iter)

    hdr_slot = f"__fordict_hdr_{id(s)}"
    len_slot = f"__fordict_len_{id(s)}"
    idx_slot = f"__fordict_idx_{id(s)}"
    ctx.alloc_slot(hdr_slot, "int")
    ctx.alloc_slot(len_slot, "int")
    ctx.alloc_slot(idx_slot, "int")

    _local_write(ctx, hdr_slot, header)
    b = ctx.builder()
    initial_len = b.load(Kind.INT, header, offset=_DICT_LEN_OFF)
    _local_write(ctx, len_slot, initial_len)
    _local_write(ctx, idx_slot, ctx.builder().const(0))

    top_blk, _ = new_block(ctx.func, "fordict_top")
    body_blk, _ = new_block(ctx.func, "fordict_body")
    cont_blk, _ = new_block(ctx.func, "fordict_cont")
    end_blk, _ = new_block(ctx.func, "fordict_end")
    if s.orelse:
        else_blk, _ = new_block(ctx.func, "fordict_else")
        exit_target = else_blk
    else:
        exit_target = end_blk

    entry_blk = ctx.block
    ctx.builder().br(top_blk)
    top_blk.preds.append(entry_blk)

    ctx.block = top_blk
    idx_v = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    len_v = _local_read(ctx, len_slot, "int")
    b = ctx.builder()
    done = b.icmp(Predicate.GE, idx_v, len_v)
    b.condbr(done, exit_target, body_blk)
    exit_target.preds.append(top_blk)
    body_blk.preds.append(top_blk)

    ctx.block = body_blk
    hdr_v = _local_read(ctx, hdr_slot, "int")
    b = ctx.builder()
    order_buf = b.load(Kind.INT, hdr_v, offset=_DICT_ORDER_OFF)
    b = ctx.builder()
    idx_v2 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    byte_off = b.shl(idx_v2, b.const(3))
    b = ctx.builder()
    elem_ptr = b.add(order_buf, byte_off)
    b = ctx.builder()
    key_v = b.load(Kind.INT, elem_ptr, offset=0)

    if s.var not in ctx.locals_:
        ctx.alloc_slot(s.var, "str")
    _local_write(ctx, s.var, key_v)

    ctx.loop_targets.append((cont_blk, end_blk))
    build_stmts(ctx, s.body)
    if ctx.block.terminator() is None:
        ctx.builder().br(cont_blk)
        cont_blk.preds.append(ctx.block)
    ctx.loop_targets.pop()

    ctx.block = cont_blk
    idx_v3 = _local_read(ctx, idx_slot, "int")
    b = ctx.builder()
    next_idx = b.add(idx_v3, b.const(1))
    _local_write(ctx, idx_slot, next_idx)
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
    # range-args vs. "for x in <iterable>" are different codegen paths entirely.
    if s.iter is None:
        _build_for_range(ctx, s)
        return
    iter_t = A.expr_type(s.iter)
    if iter_t in ("list", "tuple"):
        _build_for_list(ctx, s)
        return
    if iter_t == "str":
        _build_for_str(ctx, s)
        return
    if iter_t == "dict":
        _build_for_dict(ctx, s)
        return
    raise SSABuildError(f"For over {iter_t!r}: not yet wrapped")


def _build_list_subscript_store(
    ctx: FuncCtx, header: Value, raw_idx: Value, value: Value
) -> None:
    """Write `value` into `header[raw_idx]` with negative-index wraparound.

    Shared helper used by _build_indexassign and _build_tupleassign's
    parallel-Subscript branch. No bounds check — matches codegen.py's
    existing silent-corrupt-on-oob behaviour.
    """
    neg_blk, _ = new_block(ctx.func, "idxw_neg")
    pos_blk, _ = new_block(ctx.func, "idxw_pos")
    norm_blk, _ = new_block(ctx.func, "idxw_norm")
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

    b = ctx.builder()
    buf = b.load(Kind.INT, header, offset=_LIST_BUF_OFF)
    b = ctx.builder()
    byte_off = b.shl(norm_idx, b.const(3))
    b = ctx.builder()
    elem_ptr = b.add(buf, byte_off)
    ctx.builder().store(elem_ptr, value, offset=0)


def _build_tupleassign(ctx: FuncCtx, s: A.TupleAssign) -> None:
    # Three sub-cases matching codegen.py:2141-2302:
    #
    # (A) StarTarget form: `a, *rest = xs` — evaluate xs once, read plain
    #     slots at fixed offsets, build rest via _runtime_list_slice.
    # (B) Unpack form: `a, b = tup_or_str` (single RHS, tuple/list/str/any
    #     typed) — evaluate RHS once, extract per-slot value.
    # (C) Parallel form: `a, b = b, a` (one value per target) — evaluate
    #     every RHS into a temp slot FIRST, then commit all stores.

    has_star = any(isinstance(t, A.StarTarget) for t in s.targets)

    if has_star:
        # ---- case A: starred assignment ----
        star_i = next(i for i, t in enumerate(s.targets) if isinstance(t, A.StarTarget))
        n_before = star_i
        n_after = len(s.targets) - star_i - 1

        src = build_expr(ctx, s.values[0])
        hdr_slot = f"__tupunpack_{id(s)}"
        ctx.alloc_slot(hdr_slot, "int")
        _local_write(ctx, hdr_slot, src)

        el_t = getattr(s.values[0], "list_el_type", "int")
        el_kind = Kind.FLOAT if el_t == "float" else Kind.INT

        # Plain targets before the star
        for i in range(n_before):
            hdr_v = _local_read(ctx, hdr_slot, "int")
            b = ctx.builder()
            buf = b.load(Kind.INT, hdr_v, offset=_LIST_BUF_OFF)
            b = ctx.builder()
            elem = b.load(el_kind, buf, offset=i * 8)
            name = s.targets[i].name
            if name not in ctx.locals_:
                ctx.alloc_slot(name, el_t)
            _local_write(ctx, name, elem)

        # Plain targets after the star (read from the end)
        for j in range(n_after):
            hdr_v = _local_read(ctx, hdr_slot, "int")
            b = ctx.builder()
            lst_len = b.load(Kind.INT, hdr_v, offset=_LIST_LEN_OFF)
            b = ctx.builder()
            tail_off = b.sub(lst_len, b.const(n_after - j))
            b = ctx.builder()
            buf = b.load(Kind.INT, hdr_v, offset=_LIST_BUF_OFF)
            b = ctx.builder()
            byte_off = b.shl(tail_off, b.const(3))
            b = ctx.builder()
            elem_ptr = b.add(buf, byte_off)
            b = ctx.builder()
            elem = b.load(el_kind, elem_ptr, offset=0)
            name = s.targets[star_i + 1 + j].name
            if name not in ctx.locals_:
                ctx.alloc_slot(name, el_t)
            _local_write(ctx, name, elem)

        # Star target: list slice [n_before : len - n_after]
        hdr_v = _local_read(ctx, hdr_slot, "int")
        b = ctx.builder()
        lst_len = b.load(Kind.INT, hdr_v, offset=_LIST_LEN_OFF)
        b = ctx.builder()
        stop_v = b.sub(lst_len, b.const(n_after))
        start_v = ctx.builder().const(n_before)
        rest_v = ctx.builder().raw_asm(
            Kind.INT, [hdr_v, start_v, stop_v],
            {k: "call _runtime_list_slice" for k in _X86_64_KEYS},
        )
        rest_name = s.targets[star_i].name
        if rest_name not in ctx.locals_:
            ctx.alloc_slot(rest_name, "list")
        _local_write(ctx, rest_name, rest_v)
        return

    rhs_t = A.expr_type(s.values[0]) if len(s.values) == 1 else None
    is_unpack = len(s.values) == 1 and (
        rhs_t in ("tuple", "any", "str") or A.expr_type(s.values[0]) == "list"
    )

    if is_unpack:
        # ---- case B: single-iterable unpack ----
        src = build_expr(ctx, s.values[0])
        hdr_slot = f"__tupunpack_{id(s)}"
        ctx.alloc_slot(hdr_slot, "int")
        _local_write(ctx, hdr_slot, src)

        if rhs_t == "str":
            for i, target in enumerate(s.targets):
                ptr_v = _local_read(ctx, hdr_slot, "int")
                idx_v = ctx.builder().const(i)
                char_v = ctx.builder().raw_asm(
                    Kind.INT, [ptr_v, idx_v],
                    {k: "call _runtime_str_char_at" for k in _X86_64_KEYS},
                )
                name = target.name
                if name not in ctx.locals_:
                    ctx.alloc_slot(name, "str")
                _local_write(ctx, name, char_v)
            return

        ets = A.tuple_element_types(s.values[0])
        for i, target in enumerate(s.targets):
            el_t = ets[i] if i < len(ets) else "int"
            el_kind = Kind.FLOAT if el_t == "float" else Kind.INT
            hdr_v = _local_read(ctx, hdr_slot, "int")
            b = ctx.builder()
            buf = b.load(Kind.INT, hdr_v, offset=_LIST_BUF_OFF)
            b = ctx.builder()
            elem = b.load(el_kind, buf, offset=i * 8)
            name = target.name
            if name not in ctx.locals_:
                ctx.alloc_slot(name, el_t)
            _local_write(ctx, name, elem)
        return

    # ---- case C: parallel form ----
    # Evaluate every RHS into a temporary frame slot first (two-pass model
    # so swap works: a, b = b, a evaluates both b and a BEFORE storing).
    tmp_vals: list[Value] = []
    tmp_types: list[str] = []
    for i, v_expr in enumerate(s.values):
        v = build_expr(ctx, v_expr)
        v_t = A.expr_type(v_expr)
        tmp_name = f"__tuptmp_{id(s)}_{i}"
        ctx.alloc_slot(tmp_name, v_t)
        _local_write(ctx, tmp_name, v)
        tmp_vals.append(v)
        tmp_types.append(v_t)

    # Commit stores.
    for i, target in enumerate(s.targets):
        tmp_name = f"__tuptmp_{id(s)}_{i}"
        val_v = _local_read(ctx, tmp_name, tmp_types[i])

        if isinstance(target, A.Name):
            if target.name not in ctx.locals_:
                ctx.alloc_slot(target.name, tmp_types[i])
            _local_write(ctx, target.name, val_v)

        elif isinstance(target, A.Subscript):
            obj_t = A.expr_type(target.obj)
            if obj_t == "dict":
                key_v = build_expr(ctx, target.index)
                header_v = build_expr(ctx, target.obj)
                ctx.builder().raw_asm(
                    Kind.NONE, [header_v, key_v, val_v],
                    {k: "call _runtime_dict_set" for k in _X86_64_KEYS},
                )
            else:
                header_v = build_expr(ctx, target.obj)
                raw_idx = build_expr(ctx, target.index)
                _build_list_subscript_store(ctx, header_v, raw_idx, val_v)

        else:
            raise SSABuildError(
                f"TupleAssign parallel target type {type(target).__name__}: not yet wrapped"
            )


_STMT_BUILDERS: dict[type, Callable[[FuncCtx, object], None]] = {
    A.Assign: _build_assign,
    A.MultiAssign: _build_multiassign,
    A.TupleAssign: _build_tupleassign,
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
    A.IndexAssign: _build_indexassign,
    A.Global: _build_global,
    A.Nonlocal: _build_nonlocal,
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
