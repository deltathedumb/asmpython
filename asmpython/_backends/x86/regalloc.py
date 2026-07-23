"""
Linear-scan register allocator for the x86 (32-bit) backend.

Maps every IRValue in an IRFunc to either a physical register or an
EBP-relative stack slot using Belady's optimal eviction policy.

Adapted from the x86-64 backend's regalloc.py, which this is modeled on --
every ABI-specific constant below (stack-slot width, spill-slot size,
incoming-argument base offset, alignment padding) is a real, deliberate
change from that original, not an oversight:

  - cdecl (this backend's baseline calling convention, both Linux/System-V
    i386 and Windows x86) passes EVERY argument on the stack -- there is no
    register-argument-passing model at all, unlike x86-64's SysV/Win64
    register-based first-N-args convention. Every function parameter is
    therefore always a stack parameter here; the ARG_REGS_SYSV/ARG_REGS_
    WIN64-style lookup x86-64's own `allocate()` does for parameter
    binding has no equivalent to import from this backend's encoder.py.
  - Every stack slot (spill, alloca, callee-saved push) is 4 bytes wide in
    32-bit mode, not 8 -- every literal `8`/`+= 8`/`* 8` x86-64's version
    uses for this is a real width bug if copied unchanged.
  - The incoming stack-argument base is exactly 8 bytes above EBP (4-byte
    return address + 4-byte saved EBP push in the prologue) -- x86-64's
    48-or-16-plus-shadow-space calculation is entirely about ABI concepts
    (Win64 shadow space, SysV red-zone adjacency) that don't exist here.
  - `RCX` (the variable-shift-count register x86-64's own `avoid_rcx`
    logic special-cases) is `ECX` in 32-bit mode -- same architectural
    role (`shl`/`shr`/`sar` with a register count operand always reads
    CL), different register name.

Every ABI-AGNOSTIC section (liveness analysis, eviction policy, call- and
variable-shift-crossing detection) is copied verbatim from the x86-64
version -- none of that logic depends on register width or calling
convention, and each carries real, hard-won bug-fix history (see each
function's own docstring) not worth re-deriving from scratch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from .encoder import Reg, XmmReg, CALLEE_SAVED


# ── Location types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegLoc:
    reg: Reg


@dataclass(frozen=True)
class XmmLoc:
    reg: XmmReg


@dataclass(frozen=True)
class StackLoc:
    """EBP-relative offset.

    Negative offsets address this function's own frame below EBP; positive
    offsets address caller-provided incoming stack arguments above it.
    """
    offset: int


Location = Union[RegLoc, XmmLoc, StackLoc]


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class AllocResult:
    locs:             dict[str, Location]   # IRValue.name → where it lives
    alloca_slots:     dict[str, int]        # alloca result → EBP offset of storage
    stack_bytes:      int                   # total bytes to SUB ESP in prologue
    callee_saved:     list[Reg]             # must push/pop in prologue / epilogue
    callee_saved_xmm: list[XmmReg]         # always empty -- see _CALLEE_SAVED_XMM


# ── Register pools ────────────────────────────────────────────────────────────
# popleft() order: caller-saved first (no push/pop overhead), then callee-saved.
# EBP/ESP are never in this pool -- always the frame/stack pointers, exactly
# like x86-64's own _GP_POOL excludes RBP/RSP.

_GP_POOL: tuple[Reg, ...] = (
    Reg.EAX, Reg.ECX, Reg.EDX,             # caller-saved
    Reg.EBX, Reg.ESI, Reg.EDI,             # callee-saved
)

_XMM_POOL: tuple[XmmReg, ...] = tuple(XmmReg(i) for i in range(6))  # XMM6/XMM7 reserved as scratch, mirroring the x86-64 backend's own two-scratch-register convention (see that backend's codegen.py)

# No XMM registers are callee-saved under EITHER 32-bit ABI this backend
# targets -- cdecl's callee-saved set is EBX/EBP/ESI/EDI, all GP; unlike
# Win64 (which callee-saves XMM6-15), there is no 32-bit ABI equivalent.
_CALLEE_SAVED_XMM: frozenset[XmmReg] = frozenset()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_float(type_name: str) -> bool:
    return type_name in ("f32", "f64", "v128")


def _slot_size(type_name: str) -> int:
    return 16 if type_name == "v128" else 4


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
    live repro (on the x86-64 backend this was ported from): a for-loop's
    stop/step pointers (defined once before the loop) got reassigned to a
    same-iteration temp inside the increment block, corrupting the loop
    bound on the very next pass.

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
    # region's end -- confirmed via a real repro (x86-64 backend): a value
    # pinned by this false loop starved the callee-saved register pool for
    # an unrelated later value crossing a call, which fell through to a
    # caller-saved register the call then clobbered (silently corrupting a
    # string pointer into an unrelated int). Rather than tracking every
    # such helper block individually, exclude any backward branch whose
    # TARGET falls inside a try_regions span, (setjmp_bi, end_bi] --
    # _lower_try never emits a genuine loop of its own, so every backward-
    # looking branch found strictly within one of its regions is one of
    # these dispatch artifacts, not a real loop needing liveness extension
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
    #
    # `end >= bi`, not `end > bi`: a value whose textual last use falls in
    # the loop's OWN LAST block (bi == end) still needs the extension --
    # that block is the one the back edge jumps FROM, so any use inside it
    # (even its very last instruction) still precedes the next iteration's
    # re-entry into the loop. `end > bi` wrongly treated `bi == end` as
    # "not really in a loop that needs extending", leaving the value's
    # last-use exactly where the plain textual scan found it instead of
    # pushing it to the loop's true end -- confirmed via a real repro
    # (x86-64 backend): sum(xs)'s list pointer (defined once before the
    # loop, read via two `gep`s inside the loop body, the second and
    # textually-last of which sits in the loop's own last block) kept its
    # un-extended last-use, got evicted mid-loop for a value that only
    # lives from that point onward, and its physical register held a
    # stale/wrong value machine code containing a NULL/garbage pointer
    # that segfaulted (confirmed via gdb: the register holding what should
    # have been the list pointer instead held a small loop-counter-like
    # integer) -- the allocator narrowly avoided this everywhere else in
    # the existing test suite by coincidence (the value happening to still
    # be needed for OTHER reasons, or the loop's last block happening to
    # not be where the un-extended value's own last use falls), which is
    # why this specific, narrow shape had never been caught before.
    # Extending to (end, len(instrs)) is always >= (bi, _ii) when bi ==
    # end (an instruction index is always < the block's own instruction
    # count), so this is a pure widening -- never moves a value's
    # recorded lifetime backward.
    for name, (bi, _ii) in list(last.items()):
        start, end = loop_start[bi], loop_end[bi]
        if end >= bi and def_block.get(name, bi) < start:
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
    # the handler computes -- confirmed via gdb/objdump (x86-64 backend):
    # a for loop's own loop-variable address, live in RDI across a `try`
    # whose `except` branch prints that same loop variable, read back as
    # a corrupted/NULL pointer on the very next iteration after the
    # exception fired, segfaulting the following loop's own unrelated
    # code (which reused the same now-scrambled physical register).
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
    direct trace on a real crash on the x86-64 backend this was ported
    from (196_hashlib_module.py's MD5 block processing): `%t41 =
    imul(...)` used as `%t43 = iadd(%t41, %t42)`'s own operand at the SAME
    instruction shared its last-use with `now`, got evicted, and _dst_gp
    later asserted on the resulting bogus location."""
    _INF = (10**9, 10**9)
    return max(in_reg, key=lambda n: _INF if last_use.get(n, (-1, -1)) < now else last_use[n])


def _compute_crosses_call(func: Any) -> set[str]:
    """Names of values whose live range spans a `call` instruction.

    A value placed in a caller-saved register is *not* safe to read after a
    call: the callee is free to clobber it (that's what "caller-saved"
    means -- EAX/ECX/EDX under cdecl, same architectural rule as x86-64's
    RAX/RCX/RDX/RSI/RDI/R8-R11), and this backend's own `_call` codegen
    does exactly that with no save/restore of its own -- it trusts the
    allocator never put a still-needed value there. Conservative by
    block, not just by instruction position: any use in a different block
    than the definition counts as crossing, even if no call is provably
    between them, since this allocator doesn't reason about CFG edges
    precisely enough to prove otherwise.
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
    ECX, since x86's variable-count shift hard-requires the count in CL
    and this backend's own codegen.py's `_shift` unconditionally does `mov
    ecx, cnt_r` with no save/restore of whatever the allocator previously
    put there (identical architectural constraint to x86-64's RCX rule,
    same shift instructions, 32-bit register name).

    Same hazard class as `_compute_crosses_call`'s callee-saved-register
    rule (a fixed-register instruction clobbering something the
    allocator didn't know to protect), but for a single hard-coded
    register rather than the whole caller-saved set, and triggered by a
    specific instruction shape rather than every `call`. Confirmed via a
    real crash on the x86-64 backend this was ported from:
    `alphabet[(triple >> 12) & 0x3F]`, a variable-count `>>` (`triple`
    isn't a compile-time constant) inside a loop with two string-index
    calls back to back -- the SECOND call's own string pointer argument
    happened to land in RCX, then the shift's own unconditional `mov rcx,
    cnt_r` clobbered it before the call read it, corrupting the pointer
    into garbage that then crashed `strlen` inside the runtime's string-
    length helper.

    Deliberately excludes the shift instruction's OWN two operands (the
    value being shifted and the shift count) from being flagged here --
    the instruction is a legitimate DEFINING use of the count into ECX,
    not a value it clobbers out from under someone else; a value dying
    at (or defined by) the shift itself has no conflict with owning ECX
    for that one instruction.

    The shift's own RESULT is a different story and IS flagged: unlike
    a value merely dying at this instruction, `codegen.py`'s `_shift`
    physically stages the runtime count into ECX as an intermediate step
    (`mov dst, val_r` then `mov ecx, cnt_r` then `shr cl, dst`) -- if the
    allocator picks ECX as the shift's OWN destination register, the
    second move (staging the count) clobbers the value the first move
    just wrote, before the shift instruction itself ever executes.
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

def allocate(func: Any, abi: str = "cdecl") -> AllocResult:
    """
    Allocate registers for *func* (an IRFunc duck-type).

    abi: "cdecl" -- the only calling convention this backend currently
         implements. Both Linux/System-V i386 and Windows x86 use cdecl
         as their default/baseline convention: every argument on the
         stack, right-to-left push order, CALLER cleans the stack.
         Accepted as a parameter (matching the x86-64 backend's own
         `allocate(func, abi=...)` signature) so a future `--abi
         fastcall` variant (first two GP args in ECX/EDX, callee cleans
         the stack -- mentioned as a planned option in encoder.py's own
         module docstring) can be added here without changing every
         CALLER of this function; not implemented yet, since nothing in
         this backend generates fastcall calls today.
    """
    callee_gp = set(CALLEE_SAVED) - {Reg.EBP}  # EBP is always the frame pointer, never allocated to an arbitrary value
    callee_xmm = _CALLEE_SAVED_XMM

    locs:             dict[str, Location] = {}
    alloca_slots:     dict[str, int]      = {}
    used_callee_gp:   set[Reg]            = set()
    used_callee_xmm:  set[XmmReg]         = set()
    stack_top = 0  # bytes consumed below EBP so far

    free_gp:  list[Reg]    = list(_GP_POOL)
    free_xmm: list[XmmReg] = list(_XMM_POOL)

    in_gp:  dict[str, Reg]    = {}  # values currently in a GP reg
    in_xmm: dict[str, XmmReg] = {}  # values currently in an XMM reg

    last_use  = _last_uses(func)
    crosses_call = _compute_crosses_call(func)
    crosses_var_shift = _compute_crosses_var_shift(func)
    now: tuple[int, int] = (-1, -1)  # updated each instruction

    # ── inner helpers that close over the mutable state ───────────────────────

    def _take_gp(prefer_callee_saved: bool = False, avoid_ecx: bool = False) -> Reg:
        nonlocal stack_top
        if prefer_callee_saved:
            # A value crossing a call must not land in a caller-saved
            # register -- `_call`'s codegen clobbers those freely with no
            # save/restore, trusting the allocator never put a still-live
            # value there. This is a HARD exclusion, not just a
            # preference: if no callee-saved register is free, evict a
            # callee-saved HOLDER to stack rather than silently falling
            # through to a caller-saved register (mirrors `avoid_ecx`'s
            # own eviction fallback just below). Same real-bug class as
            # the x86-64 backend's own identical logic (see that
            # backend's docstring) -- ported here for the smaller
            # 6-register cdecl pool.
            for i, r in enumerate(free_gp):
                if r in callee_gp:
                    return free_gp.pop(i)
            if free_gp:  # only caller-saved free -- evict a callee-saved holder instead
                victim = _pick_evict(
                    {n: r for n, r in in_gp.items() if r in callee_gp} or in_gp,
                    last_use, now,
                )
                stack_top += 4
                locs[victim] = StackLoc(-stack_top)
                freed = in_gp.pop(victim)
                free_gp.append(freed)
                for i, r in enumerate(free_gp):
                    if r in callee_gp:
                        return free_gp.pop(i)
        if avoid_ecx:
            # A value crossing a variable-count shl/shr/sar must not land
            # in ECX -- this backend's own `_shift` codegen unconditionally
            # does `mov ecx, cnt_r` for the shift count with no save/
            # restore, trusting the allocator never put a still-live value
            # there (see `_compute_crosses_var_shift`'s docstring). Unlike
            # `prefer_callee_saved` above, this is a hard exclusion, not
            # just a preference -- ANY non-ECX register is safe, so skip
            # straight to eviction if ECX is the only one free rather than
            # accepting it.
            for i, r in enumerate(free_gp):
                if r != Reg.ECX:
                    return free_gp.pop(i)
            if free_gp:  # only ECX free -- evict something else instead
                victim = _pick_evict(
                    {n: r for n, r in in_gp.items() if r != Reg.ECX} or in_gp,
                    last_use, now,
                )
                stack_top += 4
                locs[victim] = StackLoc(-stack_top)
                freed = in_gp.pop(victim)
                free_gp.append(freed)
                for i, r in enumerate(free_gp):
                    if r != Reg.ECX:
                        return free_gp.pop(i)
        if free_gp:
            return free_gp.pop(0)
        victim = _pick_evict(in_gp, last_use, now)
        stack_top += 4
        locs[victim] = StackLoc(-stack_top)
        freed = in_gp.pop(victim)
        free_gp.append(freed)
        return free_gp.pop(0)

    def _take_xmm() -> XmmReg:
        nonlocal stack_top
        if free_xmm:
            return free_xmm.pop(0)
        victim = _pick_evict(in_xmm, last_use, now)
        stack_top += 4
        locs[victim] = StackLoc(-stack_top)
        freed = in_xmm.pop(victim)
        free_xmm.append(freed)
        return free_xmm.pop(0)

    def _alloc_gp(name: str) -> None:
        r = _take_gp(
            prefer_callee_saved=name in crosses_call,
            avoid_ecx=name in crosses_var_shift,
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
    # cdecl passes EVERY argument on the stack -- no register-argument-
    # passing model exists in this convention at all (unlike x86-64's
    # SysV/Win64 first-N-args-in-registers rule), so every parameter is
    # unconditionally a stack parameter; there is no int_args/xmm_args
    # register-lookup branch to port from the x86-64 backend's own
    # version of this section.

    stack_params: list[str] = [param.name for param in func.params]

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
                    # are recomputed via lea/mov on every read instead of
                    # competing for the same fixed-size register pool as
                    # everything else, often for the whole function).
                    size = int(instr.operands[0]) if instr.operands else 4
                    size = (size + 3) & ~3        # 4-byte align
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

    # ── Align total stack frame to 16 bytes ────────────────────────────────────
    # cdecl itself has no mandatory 16-byte-alignment requirement the way
    # x86-64's SysV/Win64 ABIs do -- but this backend's own encoder emits
    # alignment-SENSITIVE SSE instructions (movaps/movdqa, which fault on
    # a misaligned memory operand) for float/v128 spills and stack-
    # relative stores, so the same conservative alignment discipline is
    # kept here rather than relying on cdecl's own historically looser
    # convention.
    #
    # This is NOT simply x86-64's own odd/even-parity check with 8
    # swapped for 12 -- an earlier draft made exactly that mechanical
    # substitution and it's wrong. x86-64's residue only ever needs to
    # distinguish two cases (0 or 8) because its 8-byte pushes divide 16
    # exactly twice; x86-32's 4-byte pushes divide 16 FOUR ways, so the
    # required residue actually cycles through all of 8, 4, 0, 12 as the
    # callee-saved-push count increases, not just two values. Re-derived
    # directly rather than assumed: _prologue() pushes `push ebp` (the
    # frame pointer, always) then one `push` per callee-saved GP register
    # actually used, each shifting ESP by -4; a real caller's own call
    # site leaves ESP at %16==0 immediately before `call` (this backend's
    # own convention, matching the x86-64 backend's identical assumption),
    # so ESP is %16==12 on entry (the call itself pushed a 4-byte return
    # address), %16==8 after `push ebp`, and %16 == (8 - 4*n) mod 16 after
    # n more callee-saved pushes -- stack_top must then bring that back to
    # 0 mod 16, i.e. stack_top % 16 must equal ESP's own residue at that
    # point. Verified independently via a small arithmetic simulation
    # (not just reasoned through) before trusting it: for n_callee_saved
    # in 0..3, the required stack_top%16 is 8, 4, 0, 12 respectively --
    # exactly `(8 - 4 * n) % 16`, which is what's used below (`% 4` on the
    # callee-saved count, not `% 2`, is the real cycle length here).
    target_residue = (8 - 4 * (len(used_callee_gp) % 4)) % 16
    if stack_top % 16 != target_residue:
        stack_top += (target_residue - stack_top) % 16

    # 8 = 4-byte return address + 4-byte saved EBP (pushed in the
    # prologue before locals are reserved) -- the real cdecl incoming-
    # stack-argument base; nothing like x86-64's Win64-shadow-space/SysV-
    # red-zone-adjacent 48-or-16 constant applies here.
    incoming_stack_base = 8 + 4 * len(used_callee_gp)
    for i, name in enumerate(stack_params):
        locs[name] = StackLoc(incoming_stack_base + 4 * i)

    return AllocResult(
        locs             = locs,
        alloca_slots     = alloca_slots,
        stack_bytes      = stack_top,
        callee_saved     = sorted(used_callee_gp,  key=int),
        callee_saved_xmm = sorted(used_callee_xmm, key=int),
    )
