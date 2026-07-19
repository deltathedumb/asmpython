"""
Linear-scan register allocator for the x86-64 backend.

Maps every IRValue in an IRFunc to either a physical register or an
RBP-relative stack slot using Belady's optimal eviction policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from .encoder import (
    Reg, XmmReg,
    ARG_REGS_SYSV, ARG_REGS_WIN64,
    XMM_ARG_SYSV, XMM_ARG_WIN64,
    CALLEE_SAVED_SYSV, CALLEE_SAVED_WIN64,
)


# ── Location types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegLoc:
    reg: Reg


@dataclass(frozen=True)
class XmmLoc:
    reg: XmmReg


@dataclass(frozen=True)
class StackLoc:
    """RBP-relative offset.

    Negative offsets address this function's own frame below RBP; positive
    offsets address caller-provided incoming stack arguments above it.
    """
    offset: int


Location = Union[RegLoc, XmmLoc, StackLoc]


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class AllocResult:
    locs:             dict[str, Location]   # IRValue.name → where it lives
    alloca_slots:     dict[str, int]        # alloca result → RBP offset of storage
    stack_bytes:      int                   # total bytes to SUB RSP in prologue
    callee_saved:     list[Reg]             # must push/pop in prologue / epilogue
    callee_saved_xmm: list[XmmReg]         # Win64: XMM6-15 if clobbered


# ── Register pools ────────────────────────────────────────────────────────────
# popleft() order: caller-saved first (no push/pop overhead), then callee-saved

_GP_POOL: tuple[Reg, ...] = (
    Reg.R9,  Reg.R8,                                # caller-saved (R10/R11 reserved as scratch)
    Reg.RDI, Reg.RSI, Reg.RDX, Reg.RCX, Reg.RAX,  # caller-saved
    Reg.RBX, Reg.R12, Reg.R13, Reg.R14, Reg.R15,  # callee-saved
)

_XMM_POOL: tuple[XmmReg, ...] = tuple(XmmReg(i) for i in range(14))  # XMM14/XMM15 reserved as scratch (two, mirroring _SCRATCH/_SCRATCH2 on the GP side -- see codegen.py's _xmm's alt_scratch docstring for why a second one is required)

# XMM6-XMM15 are callee-saved under Win64 ABI
_WIN64_CALLEE_XMM: frozenset[XmmReg] = frozenset(XmmReg(i) for i in range(6, 16))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_float(type_name: str) -> bool:
    return type_name in ("f32", "f64", "v128")


def _slot_size(type_name: str) -> int:
    return 16 if type_name == "v128" else 8


def _last_uses(func: Any) -> dict[str, tuple[int, int]]:
    """Return the (block_idx, instr_idx) of each value's final use.

    A value defined *outside* a loop and read inside it must stay live for
    the whole loop, not just until its textually-last mention: a back edge
    (a br/br.t target at or before the branching block) means every block
    from the target through the branching block re-executes each
    iteration, and since the value's own definition is outside that range,
    nothing inside the loop refreshes it -- it's still needed on the next
    pass even after its "last" textual use. Scanning blocks in plain list
    order misses this: it sees that last mention, concludes the value is
    dead, and lets the allocator reuse its register for something else --
    correct for the textual order, wrong for control flow. Confirmed via a
    live repro: a for-loop's stop/step pointers (defined once before the
    loop) got reassigned to a same-iteration temp inside the increment
    block, corrupting the loop bound on the very next pass.

    This must NOT extend values whose definition is itself inside the same
    loop (the common case: nearly every temporary a loop body computes) --
    those are refreshed every iteration before they're read again, so their
    ordinary textual last-use is already correct, and over-extending them
    would make almost everything in a loop "alive" simultaneously and
    exhaust the register pool for no reason.
    """
    label_to_idx = {b.label: bi for bi, b in enumerate(func.blocks)}

    # try/except lowering (ir_lower.py's _lower_try) produces MULTIPLE
    # backward-by-block-index branches that are NOT loops:
    #  - a normal-completion `br` back to the try's own `end_b` (created
    #    EARLY, right after handler_b/body_b, before the per-handler
    #    check_blocks) from a later-indexed block (the body-ok path, a
    #    matched-handler path, the re-raise path) -- the try's own
    #    convergent exit point.
    #  - a per-handler type-match `br.t` whose "matched" target
    #    (`try_run_*`, created BEFORE the mid-loop that builds the
    #    `try_check_next_*` chain checking each candidate exception id)
    #    sits at a LOWER index than the `br.t` emitting it, once more than
    #    one exception id needs checking -- a one-shot dispatch, not a
    #    loop, but backward by index all the same.
    # Without excluding these, the loop-back-edge scan below misidentifies
    # the whole enclosing span as one "loop", force-extending the liveness
    # of every value referenced anywhere in it (including inside handler
    # blocks that never execute on the taken path) all the way to the
    # region's end -- confirmed via a real repro: a value pinned by this
    # false loop starved the callee-saved register pool for an unrelated
    # later value crossing a call, which fell through to a caller-saved
    # register the call then clobbered (silently corrupting a string
    # pointer into an unrelated int). Rather than tracking every such
    # helper block individually, exclude any backward branch whose TARGET
    # falls inside a try_regions span, (setjmp_bi, end_bi] -- _lower_try
    # never emits a genuine loop of its own, so every backward-looking
    # branch found strictly within one of its regions is one of these
    # dispatch artifacts, not a real loop needing liveness extension
    # (nested loops inside a try BODY are unaffected: their own back edges
    # target blocks the loop itself created, at or after the try's
    # setjmp_bi, but a genuine loop's back edge stays within the loop's
    # own narrower span and this broader try-region exclusion only ever
    # widens what's IGNORED as a loop, never suppresses a real one whose
    # target lies outside every try_regions span).
    try_regions = getattr(func, "try_regions", ())

    def _in_try_region(idx: int) -> bool:
        return any(setjmp_bi < idx <= end_bi for setjmp_bi, end_bi in try_regions)

    # For each block bi, the [start, end] of the widest loop containing it
    # (its own index for both if bi isn't in any loop). A back edge from
    # block `src` to block `dst` (dst <= src) means every block in
    # [dst, src] belongs to one loop spanning exactly that range;
    # overlapping/nested loops just take the widest start/end seen.
    loop_start = list(range(len(func.blocks)))
    loop_end = list(range(len(func.blocks)))
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.op not in ("br", "br.t"):
                continue
            targets = instr.operands[1:] if instr.op == "br.t" else instr.operands
            for t in targets:
                ti = label_to_idx.get(str(t))
                if ti is not None and ti <= bi and not _in_try_region(ti):
                    for k in range(ti, bi + 1):
                        if loop_start[k] > ti:
                            loop_start[k] = ti
                        if loop_end[k] < bi:
                            loop_end[k] = bi

    def_block: dict[str, int] = {}
    for param in func.params:
        def_block[param.name] = 0
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.result is not None:
                def_block.setdefault(instr.result.name, bi)

    last: dict[str, tuple[int, int]] = {}
    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            for op in instr.operands:
                if hasattr(op, "name"):
                    last[op.name] = (bi, ii)

    # Extend a use inside a loop to that loop's last block, but only when
    # the value's definition lies outside the loop's own range -- see the
    # docstring for why a loop-internal definition doesn't need this.
    for name, (bi, _ii) in list(last.items()):
        start, end = loop_start[bi], loop_end[bi]
        if end > bi and def_block.get(name, bi) < start:
            last[name] = (end, len(func.blocks[end].instrs))

    # try/except handler blocks are reached via an IMPLICIT control
    # transfer -- a `call _abi_setjmp` followed by a br.t on its result,
    # where the "handler taken" edge only becomes real at runtime via
    # `_abi_raise` -> `_runtime_longjmp` jumping directly back to the
    # setjmp call site, with NO ordinary br/br.t edge from inside the try
    # body to the handler blocks for the plain last-use scan above to
    # see. Worse, ir_lower.py's _lower_try (via ctx.new_block()) allocates
    # the handler blocks AFTER the try's own continuation block in the
    # function's block list (e.g. a for-loop's `Lforcont` increment
    # block, right after the loop body, ends up EARLIER in block-list
    # order than the handler blocks that conceptually run BEFORE it on
    # the exception path) -- so a value defined before the try (e.g. the
    # loop variable's global address, computed once before the loop and
    # reused every iteration) that is ALSO read inside the handler looked,
    # to a plain textual scan, already dead by the time the handler
    # block's def/use got recorded: its last "real" use was the loop's
    # own continuation block, which sits earlier in the list. The
    # allocator then freely reused that value's register for something
    # the handler computes -- confirmed via gdb/objdump: a for loop's own
    # loop-variable address, live in RDI across a `try` whose `except`
    # branch prints that same loop variable, read back as a corrupted/
    # NULL pointer on the very next iteration after the exception fired,
    # segfaulting the following loop's own unrelated code (which reused
    # the same now-scrambled physical register).
    #
    # Fix: ir_lower.py stamps the exact [setjmp_block_index,
    # end_block_index] span per try statement onto IRFunc.try_regions
    # (computed directly from the blocks it just created -- no label
    # parsing needed). Only extend values actually REFERENCED somewhere
    # inside the region (setjmp_bi, end_bi] -- e.g. read inside a handler
    # block -- to stay live through the region's end; a value merely
    # defined earlier and last used before the try even starts (the
    # common case: nearly everything in the try's own condition/header
    # blocks) has no reason to be pinned all the way through the handler
    # and must NOT be force-extended, or it wrongly outlives its real
    # last use and starves the allocator's register pool / crashes the
    # "GP result expected" assert when a short-lived value like a `br.t`
    # condition gets evicted to a stack slot it was never meant to need.
    try_regions = getattr(func, "try_regions", ())
    if try_regions:
        region_referenced: dict[int, set[str]] = {}
        for bi, block in enumerate(func.blocks):
            for instr in block.instrs:
                for op in instr.operands:
                    if not hasattr(op, "name"):
                        continue
                    for ri, (setjmp_bi, end_bi) in enumerate(try_regions):
                        if setjmp_bi < bi <= end_bi:
                            region_referenced.setdefault(ri, set()).add(op.name)
        for name, (bi, _ii) in list(last.items()):
            db = def_block.get(name, bi)
            for ri, (setjmp_bi, end_bi) in enumerate(try_regions):
                if (
                    db <= setjmp_bi
                    and name in region_referenced.get(ri, ())
                    and end_bi > last[name][0]
                ):
                    last[name] = (end_bi, len(func.blocks[end_bi].instrs))
    return last


def _pick_evict(in_reg: dict[str, Any],
                last_use: dict[str, tuple[int, int]],
                now: tuple[int, int]) -> str:
    """Belady: evict the value used furthest in the future (or already dead).

    `<` not `<=` against `now`: a value whose last use IS `now` is being
    read at this very instruction (e.g. as an operand of the instruction
    whose own destination we're allocating a register for) -- not yet
    dead. Using `<=` treated such a value as already-dead-priority
    (_INF), so it could get evicted out from under an instruction still
    reading it as an operand, corrupting the result. Confirmed via a
    direct trace on a real crash (196_hashlib_module.py's MD5 block
    processing): `%t41 = imul(...)` used as `%t43 = iadd(%t41, %t42)`'s
    own operand at the SAME instruction shared its last-use with `now`,
    got evicted, and _dst_gp later asserted on the resulting bogus
    location."""
    _INF = (10**9, 10**9)
    return max(in_reg, key=lambda n: _INF if last_use.get(n, (-1, -1)) < now else last_use[n])


def _compute_crosses_call(func: Any) -> set[str]:
    """Names of values whose live range spans a `call` instruction.

    A value placed in a caller-saved register is *not* safe to read after a
    call: the callee is free to clobber it (that's what "caller-saved"
    means), and `_call`'s codegen does exactly that with no save/restore of
    its own -- it trusts the allocator never put a still-needed value
    there. Conservative by block, not just by instruction position: any use
    in a different block than the definition counts as crossing, even if no
    call is provably between them, since this allocator doesn't reason
    about CFG edges precisely enough to prove otherwise.
    """
    def_pos: dict[str, tuple[int, int]] = {}
    use_positions: dict[str, list[tuple[int, int]]] = {}
    call_positions: list[tuple[int, int]] = []

    for param in func.params:
        def_pos[param.name] = (0, -1)  # available from before block 0's first instr

    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            if instr.op == "call":
                call_positions.append((bi, ii))
            if instr.result is not None:
                def_pos.setdefault(instr.result.name, (bi, ii))
            for op in instr.operands:
                if hasattr(op, "name"):
                    use_positions.setdefault(op.name, []).append((bi, ii))

    crosses: set[str] = set()
    for name, uses in use_positions.items():
        db, di = def_pos.get(name, (0, -1))
        for (ub, ui) in uses:
            if ub != db:
                crosses.add(name)
                break
            if any(cb == db and di < ci < ui for (cb, ci) in call_positions):
                crosses.add(name)
                break
    return crosses


def _compute_crosses_var_shift(func: Any) -> set[str]:
    """Names of values whose live range spans a variable-count `shl`/`shr`/
    `sar` instruction (one whose shift-count operand is a runtime value,
    not a compile-time constant) -- these values must not be allocated to
    RCX, since x86's variable-count shift hard-requires the count in CL
    and `codegen.py`'s `_shift` unconditionally does `mov rcx, cnt_r`
    with no save/restore of whatever the allocator previously put there.

    Same hazard class as `_compute_crosses_call`'s callee-saved-register
    rule (a fixed-register instruction clobbering something the
    allocator didn't know to protect), but for a single hard-coded
    register rather than the whole caller-saved set, and triggered by a
    specific instruction shape rather than every `call`. Confirmed via a
    real crash: `alphabet[(triple >> 12) & 0x3F]`, a variable-count `>>`
    (`triple` isn't a compile-time constant) inside a loop with two
    string-index calls back to back -- the SECOND call's own string
    pointer argument happened to land in RCX, then the shift's own
    unconditional `mov rcx, cnt_r` clobbered it before the call read it,
    corrupting the pointer into garbage that then crashed `strlen`
    inside the runtime's string-length helper.

    Deliberately excludes the shift instruction's OWN two operands (the
    value being shifted and the shift count) from being flagged here --
    the instruction is a legitimate DEFINING use of the count into RCX,
    not a value it clobbers out from under someone else; a value dying
    at (or defined by) the shift itself has no conflict with owning RCX
    for that one instruction.

    The shift's own RESULT is a different story and IS flagged: unlike
    a value merely dying at this instruction, `codegen.py`'s `_shift`
    physically stages the runtime count into RCX as an intermediate step
    (`mov dst, val_r` then `mov rcx, cnt_r` then `shr cl, dst`) -- if the
    allocator picks RCX as the shift's OWN destination register, the
    second move (staging the count) clobbers the value the first move
    just wrote, before the shift instruction itself ever executes.
    Confirmed via disassembly of a real corruption: a variable-count
    `(v >> 8) & 0xFF` byte extraction, with the shift's result allocated
    to RCX, produced `mov rcx, v` / `mov rcx, 8` (clobbering v) / `shr
    cl, rcx` -- computing `8 >> 8` (zero) instead of `v >> 8`. Every 4th
    value in a 4-shift extraction loop landed in RCX by allocator chance,
    matching the corruption's exact stride.
    """
    def_pos: dict[str, tuple[int, int]] = {}
    use_positions: dict[str, list[tuple[int, int]]] = {}
    var_shift_positions: list[tuple[int, int]] = []

    for param in func.params:
        def_pos[param.name] = (0, -1)

    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            if instr.op in ("shl", "shr", "sar") and not isinstance(instr.operands[1], int):
                var_shift_positions.append((bi, ii))
            if instr.result is not None:
                def_pos.setdefault(instr.result.name, (bi, ii))
            for op in instr.operands:
                if hasattr(op, "name"):
                    use_positions.setdefault(op.name, []).append((bi, ii))

    shift_operand_names: set[str] = set()
    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            if (bi, ii) in var_shift_positions:
                for op in instr.operands:
                    if hasattr(op, "name"):
                        shift_operand_names.add(op.name)

    crosses: set[str] = set()
    for name, uses in use_positions.items():
        if name in shift_operand_names:
            continue
        db, di = def_pos.get(name, (0, -1))
        for (ub, ui) in uses:
            if ub != db:
                crosses.add(name)
                break
            if any(cb == db and di < si < ui for (cb, si) in var_shift_positions):
                crosses.add(name)
                break

    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            if (bi, ii) in var_shift_positions and instr.result is not None:
                crosses.add(instr.result.name)
    return crosses


# ── Main allocator ────────────────────────────────────────────────────────────

def allocate(func: Any, abi: str = "sysv") -> AllocResult:
    """
    Allocate registers for *func* (an IRFunc duck-type).

    abi: "sysv"  — Linux / macOS System V AMD64
         "win64" — Windows x64
    """
    int_args    = ARG_REGS_WIN64   if abi == "win64" else ARG_REGS_SYSV
    xmm_args    = XMM_ARG_WIN64    if abi == "win64" else XMM_ARG_SYSV
    callee_gp   = set(CALLEE_SAVED_WIN64 if abi == "win64" else CALLEE_SAVED_SYSV)
    callee_xmm  = _WIN64_CALLEE_XMM if abi == "win64" else frozenset()

    locs:             dict[str, Location] = {}
    alloca_slots:     dict[str, int]      = {}
    used_callee_gp:   set[Reg]            = set()
    used_callee_xmm:  set[XmmReg]         = set()
    stack_top = 0  # bytes consumed below RBP so far

    free_gp:  list[Reg]    = list(_GP_POOL)
    free_xmm: list[XmmReg] = list(_XMM_POOL)

    in_gp:  dict[str, Reg]    = {}  # values currently in a GP reg
    in_xmm: dict[str, XmmReg] = {}  # values currently in an XMM reg

    last_use  = _last_uses(func)
    crosses_call = _compute_crosses_call(func)
    crosses_var_shift = _compute_crosses_var_shift(func)
    now: tuple[int, int] = (-1, -1)  # updated each instruction

    # ── inner helpers that close over the mutable state ───────────────────────

    def _take_gp(prefer_callee_saved: bool = False, avoid_rcx: bool = False) -> Reg:
        nonlocal stack_top
        if prefer_callee_saved:
            # A value crossing a call must not land in a caller-saved
            # register -- `_call`'s codegen clobbers those freely with no
            # save/restore, trusting the allocator never put a still-live
            # value there. This is a HARD exclusion, not just a
            # preference: if no callee-saved register is free, evict a
            # callee-saved HOLDER to stack rather than silently falling
            # through to a caller-saved register (mirrors `avoid_rcx`'s
            # own eviction fallback just below). Confirmed via a real
            # repro and gdb trace: with high enough register pressure
            # (three prior sorted()/min(key=)/max(key=) calls in the
            # same function), every callee-saved GP register was already
            # occupied by the time a 4th call-crossing value (a list's
            # `len`, read before a loop, used again after a `call
            # _abi_str_cmp` inside it) needed one -- the old code fell
            # through to RAX, which `_abi_str_cmp`'s own return value
            # then clobbered one iteration later, corrupting the loop's
            # own bound check and silently truncating it to one pass.
            for i, r in enumerate(free_gp):
                if r in callee_gp:
                    return free_gp.pop(i)
            if free_gp:  # only caller-saved free -- evict a callee-saved holder instead
                victim = _pick_evict(
                    {n: r for n, r in in_gp.items() if r in callee_gp} or in_gp,
                    last_use, now,
                )
                stack_top += 8
                locs[victim] = StackLoc(-stack_top)
                freed = in_gp.pop(victim)
                free_gp.append(freed)
                for i, r in enumerate(free_gp):
                    if r in callee_gp:
                        return free_gp.pop(i)
        if avoid_rcx:
            # A value crossing a variable-count shl/shr/sar must not land
            # in RCX -- `_shift`'s codegen unconditionally does `mov rcx,
            # cnt_r` for the shift count with no save/restore, trusting
            # the allocator never put a still-live value there (see
            # `_compute_crosses_var_shift`'s docstring for the real crash
            # this was found from). Unlike `prefer_callee_saved` above,
            # this is a hard exclusion, not just a preference -- ANY
            # non-RCX register is safe, so skip straight to eviction if
            # RCX is the only one free rather than accepting it.
            for i, r in enumerate(free_gp):
                if r != Reg.RCX:
                    return free_gp.pop(i)
            if free_gp:  # only RCX free -- evict something else instead
                victim = _pick_evict(
                    {n: r for n, r in in_gp.items() if r != Reg.RCX} or in_gp,
                    last_use, now,
                )
                stack_top += 8
                locs[victim] = StackLoc(-stack_top)
                freed = in_gp.pop(victim)
                free_gp.append(freed)
                for i, r in enumerate(free_gp):
                    if r != Reg.RCX:
                        return free_gp.pop(i)
        if free_gp:
            return free_gp.pop(0)
        victim = _pick_evict(in_gp, last_use, now)
        stack_top += 8
        locs[victim] = StackLoc(-stack_top)
        freed = in_gp.pop(victim)
        free_gp.append(freed)
        return free_gp.pop(0)

    def _take_xmm() -> XmmReg:
        nonlocal stack_top
        if free_xmm:
            return free_xmm.pop(0)
        victim = _pick_evict(in_xmm, last_use, now)
        stack_top += 8
        locs[victim] = StackLoc(-stack_top)
        freed = in_xmm.pop(victim)
        free_xmm.append(freed)
        return free_xmm.pop(0)

    def _alloc_gp(name: str) -> None:
        r = _take_gp(
            prefer_callee_saved=name in crosses_call,
            avoid_rcx=name in crosses_var_shift,
        )
        locs[name] = RegLoc(r)
        in_gp[name] = r
        if r in callee_gp:
            used_callee_gp.add(r)

    def _alloc_xmm(name: str) -> None:
        x = _take_xmm()
        locs[name] = XmmLoc(x)
        in_xmm[name] = x
        if x in callee_xmm:
            used_callee_xmm.add(x)

    def _free_if_dead(op: Any, pos: tuple[int, int]) -> None:
        if not hasattr(op, "name"):
            return
        n = op.name
        if last_use.get(n) != pos:
            return
        if n in in_gp:
            free_gp.append(in_gp.pop(n))
        elif n in in_xmm:
            free_xmm.append(in_xmm.pop(n))

    # ── Assign function parameters to ABI entry locations ─────────────────────

    stack_params: list[str] = []
    int_i = xmm_i = 0
    for arg_i, param in enumerate(func.params):
        if _is_float(param.type.name):
            if abi == "win64":
                if arg_i < len(xmm_args):
                    x = xmm_args[arg_i]
                    locs[param.name] = XmmLoc(x)
                    in_xmm[param.name] = x
                    if x in free_xmm:
                        free_xmm.remove(x)
                else:
                    stack_params.append(param.name)
            else:
                if xmm_i < len(xmm_args):
                    x = xmm_args[xmm_i]; xmm_i += 1
                    locs[param.name] = XmmLoc(x)
                    in_xmm[param.name] = x
                    if x in free_xmm:
                        free_xmm.remove(x)
                else:
                    stack_params.append(param.name)
        else:
            if abi == "win64":
                if arg_i < len(int_args):
                    r = int_args[arg_i]
                    locs[param.name] = RegLoc(r)
                    in_gp[param.name] = r
                    if r in free_gp:
                        free_gp.remove(r)
                else:
                    stack_params.append(param.name)
            else:
                if int_i < len(int_args):
                    r = int_args[int_i]; int_i += 1
                    locs[param.name] = RegLoc(r)
                    in_gp[param.name] = r
                    if r in free_gp:
                        free_gp.remove(r)
                else:
                    stack_params.append(param.name)

    # ── Walk all instructions ─────────────────────────────────────────────────

    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            now = (bi, ii)
            result = instr.result

            if result is not None and result.name not in locs:
                if instr.op == "alloca":
                    # Reserve stack storage; the result name resolves via
                    # alloca_slots, not a register or spill slot of its own
                    # -- see codegen.py's `_gp` for why (alloca'd pointers
                    # are recomputed via lea on every read instead of
                    # competing for the same fixed-size register pool as
                    # everything else, often for the whole function).
                    size = int(instr.operands[0]) if instr.operands else 8
                    size = (size + 7) & ~7        # 8-byte align
                    stack_top += size
                    alloca_slots[result.name] = -stack_top
                elif instr.op == "call" and result.name in crosses_call:
                    stack_top += _slot_size(result.type.name)
                    locs[result.name] = StackLoc(-stack_top)
                elif _is_float(result.type.name):
                    _alloc_xmm(result.name)
                else:
                    _alloc_gp(result.name)

            # Return registers to the free pool for operands that die here
            for op in instr.operands:
                _free_if_dead(op, now)

    # Win64 callers must always leave >= 32 bytes of *free* shadow space
    # directly below RSP at any call site -- the callee is allowed to spill
    # its own register args there. This must be EXTRA space beyond whatever
    # locals/spills already occupy, not just a floor on the total frame
    # size: every alloca/spill slot above is placed at -stack_top (i.e.
    # right next to where RSP will end up), so merely clamping stack_top to
    # a minimum of 32 leaves those slots sitting *inside* [RSP, RSP+32) --
    # exactly the region a callee like printf's varargs spill path will
    # scribble over, corrupting whatever local happened to land there.
    # Unconditionally adding 32 guarantees every existing slot (offset <=
    # -stack_top_before) ends up at <= -(32) from the new RSP, clear of the
    # shadow zone. Confirmed via a live repro: a 3-slot (24-byte) frame
    # making a libc call corrupted two of the three locals before this fix.
    if abi == "win64":
        stack_top += 32

    # ── Align total stack frame to 16 bytes (ABI requirement at call sites) ───
    # _prologue() pushes (len(callee_saved) + 1) registers (the +1 is RBP)
    # *before* subtracting stack_top, so the residue stack_top must land on
    # depends on that count's parity: an odd number of callee-saved pushes
    # needs stack_top % 16 == 8 to leave RSP 16-aligned before call sites;
    # an even count (including zero) needs stack_top % 16 == 0. Always
    # rounding to 0 here (regardless of parity) silently breaks alignment
    # whenever exactly 1 (or 3, 5, ...) callee-saved GP registers are used
    # and stack_top was already a multiple of 16 (most commonly 0) -- every
    # call in the function then runs with RSP off by 8, which corrupts
    # SSE-using libc internals (segfault) while plain integer code happens
    # to tolerate it.
    target_residue = 8 if len(used_callee_gp) % 2 == 1 else 0
    if stack_top % 16 != target_residue:
        stack_top += (target_residue - stack_top) % 16

    incoming_stack_base = (
        48 if abi == "win64" else 16
    ) + 8 * len(used_callee_gp)
    for i, name in enumerate(stack_params):
        locs[name] = StackLoc(incoming_stack_base + 8 * i)

    return AllocResult(
        locs             = locs,
        alloca_slots     = alloca_slots,
        stack_bytes      = stack_top,
        callee_saved     = sorted(used_callee_gp,  key=int),
        callee_saved_xmm = sorted(used_callee_xmm, key=int),
    )
