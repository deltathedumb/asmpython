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

_XMM_POOL: tuple[XmmReg, ...] = tuple(XmmReg(i) for i in range(15))  # XMM15 reserved as scratch

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
                if ti is not None and ti <= bi:
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
    return last


def _pick_evict(in_reg: dict[str, Any],
                last_use: dict[str, tuple[int, int]],
                now: tuple[int, int]) -> str:
    """Belady: evict the value used furthest in the future (or already dead)."""
    _INF = (10**9, 10**9)
    return max(in_reg, key=lambda n: _INF if last_use.get(n, (-1, -1)) <= now else last_use[n])


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
    now: tuple[int, int] = (-1, -1)  # updated each instruction

    # ── inner helpers that close over the mutable state ───────────────────────

    def _take_gp(prefer_callee_saved: bool = False) -> Reg:
        nonlocal stack_top
        if prefer_callee_saved:
            # A value crossing a call must not land in a caller-saved
            # register -- `_call`'s codegen clobbers those freely with no
            # save/restore, trusting the allocator never put a still-live
            # value there. Prefer any free callee-saved register first;
            # only fall through to the normal pool if none is free (rare
            # enough in practice that the residual risk is acceptable
            # rather than building full eviction-aware callee-saved
            # reservation for it).
            for i, r in enumerate(free_gp):
                if r in callee_gp:
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
        r = _take_gp(prefer_callee_saved=name in crosses_call)
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
