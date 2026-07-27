"""
Linear-scan register allocator for the ARM64 (AArch64) backend.

Maps every IRValue in an IRFunc to either a physical register or an
X29(FP)-relative stack slot using Belady's optimal eviction policy. Ported
from asmpython/_backends/x86_64/regalloc.py -- the linear-scan algorithm,
loop-liveness extension (`_last_uses`), Belady eviction (`_pick_evict`),
and call-crossing analysis (`_compute_crosses_call`) are all architecture-
generic and carried over with no behavioral change (only the imported
register/ABI constants differ). Two things are DELETED, not ported,
because they don't apply to AArch64 at all:

  - `_compute_crosses_var_shift` / `avoid_rcx`: x86's variable-count
    shift instructions hard-require the count in CL (RCX's low byte),
    which is why the x86-64 allocator has to protect that one register
    from eviction pressure. AArch64's LSL/LSR/ASR (register form) take
    the shift count as an ordinary GPR operand -- no fixed register is
    involved, so this whole hazard class doesn't exist here.
  - Win64 shadow-space padding (`stack_top += 32` for abi == "win64") and
    the SysV/Win64 ABI branch generally: AArch64 has exactly one calling
    convention this backend targets initially (AAPCS64 — the same
    argument-register assignment Linux ARM64 and Windows ARM64 both use,
    unlike x86-64 where SysV and Win64 genuinely diverge on which
    registers carry which argument). A `--target windows-arm64` variant
    may need its own small adjustments later (chiefly: Windows ARM64
    does NOT guarantee a SysV-style "red zone"-free stack the way this
    module currently assumes nothing beyond AAPCS64 itself) -- not
    attempted here; see roadmap.md's ARM64 Stage 1 scope note.

Frame pointer convention: X29 (FP) always points at the saved [FP, LR]
pair pushed by the prologue (AAPCS64's frame-record convention, so a
debugger/unwinder can walk stack frames).
Locals/spills are addressed as small negative offsets from FP, mirroring
RBP-relative addressing on the x86-64 side -- StackLoc's meaning (negative
= this function's own frame, positive = incoming stack args above the
frame record) is unchanged from the x86-64 module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from ..._compiler.cfg import try_regions_resolved
from .encoder import (
    Reg, VReg,
    ARG_REGS, FP_ARG_REGS,
    CALLEE_SAVED, CALLEE_SAVED_FP,
)


# ── Location types ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RegLoc:
    reg: Reg


@dataclass(frozen=True)
class VLoc:
    reg: VReg


@dataclass(frozen=True)
class StackLoc:
    """X29(FP)-relative offset.

    Negative offsets address this function's own frame below the saved
    [FP, LR] pair; positive offsets address caller-provided incoming
    stack arguments above it. Mirrors x86_64/regalloc.py's StackLoc
    (RBP-relative there) exactly in spirit -- FP is AArch64's equivalent
    fixed frame-base register.
    """
    offset: int


Location = Union[RegLoc, VLoc, StackLoc]


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class AllocResult:
    locs:          dict[str, Location]  # IRValue.name → where it lives
    alloca_slots:  dict[str, int]       # alloca result → FP offset of storage
    stack_bytes:   int                  # total bytes to SUB SP in prologue
    callee_saved:  list[Reg]            # must save/restore in prologue/epilogue
    callee_saved_fp: list[VReg]         # D8-D15 if clobbered


# ── Register pools ────────────────────────────────────────────────────────────
# popleft() order: caller-saved first (no save/restore overhead), then
# callee-saved. X9-X15 are caller-saved temporaries under AAPCS64; X16/X17
# are the platform's own intra-procedure-call scratch registers (IP0/IP1,
# used by the linker for veneers/PLT stubs) and are deliberately excluded
# from this pool entirely, not just reserved as backend scratch, since a
# static linker can legitimately clobber them between any two of this
# function's own instructions. X18 is the platform register on some AArch64
# ABIs (reserved for the OS -- e.g. the shadow-call-stack pointer on
# Android/some Linux configurations) and is excluded for the same
# not-actually-ours-to-use reason. X19-X28 are callee-saved.
_GP_POOL: tuple[Reg, ...] = (
    Reg.X9,  Reg.X10, Reg.X11, Reg.X12,             # caller-saved (X13-X15 reserved as scratch, mirroring x86-64's R10/R11 pair -- two scratch regs, see codegen.py's alt_scratch convention once codegen.py exists)
    Reg.X0,  Reg.X1,  Reg.X2,  Reg.X3,               # caller-saved (also arg regs -- fine to reuse once a param's value has been copied out or the param itself is done being read as an argument)
    Reg.X4,  Reg.X5,  Reg.X6,  Reg.X7,  Reg.X8,      # caller-saved
    Reg.X19, Reg.X20, Reg.X21, Reg.X22, Reg.X23,     # callee-saved
    Reg.X24, Reg.X25, Reg.X26, Reg.X27, Reg.X28,     # callee-saved
)

# V8-V15's low 64 bits (D8-D15) are callee-saved; V16-V31 are caller-saved.
# V14/V15 reserved as scratch (mirroring the GP pool's X13-X15 reservation
# and the x86-64 backend's XMM14/XMM15 scratch pair).
_FP_POOL: tuple[VReg, ...] = (
    VReg.V16, VReg.V17, VReg.V18, VReg.V19, VReg.V20, VReg.V21, VReg.V22, VReg.V23,  # caller-saved
    VReg.V24, VReg.V25, VReg.V26, VReg.V27, VReg.V28, VReg.V29, VReg.V30, VReg.V31,  # caller-saved
    VReg.V0, VReg.V1, VReg.V2, VReg.V3, VReg.V4, VReg.V5, VReg.V6, VReg.V7,          # caller-saved, also FP arg regs
    VReg.V8, VReg.V9, VReg.V10, VReg.V11, VReg.V12, VReg.V13,                        # callee-saved (V14/V15 reserved above)
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_float(type_name: str) -> bool:
    return type_name in ("f32", "f64", "v128")


def _slot_size(type_name: str) -> int:
    return 16 if type_name == "v128" else 8


def _last_uses(func: Any) -> dict[str, tuple[int, int]]:
    """Return the (block_idx, instr_idx) of each value's final use.

    Kept in step with x86_64/regalloc.py's `_last_uses` -- this is pure
    IR-level analysis (natural-loop detection, loop-liveness extension, the
    try-region liveness extension) with no x86-specific reasoning anywhere in
    it. See that module's docstring for the full rationale and the real
    production bugs (`end >= bi` off-by-one; try-region handler-block implicit
    control transfer; loop-carried values read before their own definition)
    this logic exists to avoid reintroducing.

    Loop structure comes from the shared CFG analysis (_compiler/cfg.py), the
    same as x86-64. It previously came from a block-INDEX RANGE, which invented
    loops (try/except dispatch branches jump backward by index without being
    loops -- the only reason the `try_regions` exclusion that used to sit here
    existed) and missed loop bodies (ir_lower emits helper blocks at HIGHER
    indices than the latch, so real body blocks fell outside the assumed span
    and a loop accumulator could silently reset). A branch is a back edge only
    when its target dominates its source, so both are gone by construction.
    """

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

    # Extend a use inside a loop to that loop's last block. Two cases need it:
    # a value defined OUTSIDE the loop (nothing refreshes it, so the next
    # iteration still needs it), and a value defined INSIDE the loop at a later
    # block than the use -- the allocator walks blocks in index order, so a use
    # preceding its own definition can only be reading the previous iteration's
    # value. Phi elimination creates exactly that shape: the back-edge copy sits
    # in the latch while the value is computed in a body block emitted later.
    # A value defined in the loop and used only after its definition is
    # refreshed every iteration and must NOT be extended, or nearly every loop
    # temporary is pinned live at once and the register pool is exhausted.
    # Loop liveness uses the block-INDEX SPAN of a backward-looking branch, with
    # try/except dispatch excluded. This is the rule that shipped before the
    # natural-loop rework and it is restored deliberately.
    #
    # cfg.py's dominance-based analysis is the correct description of a LOOP, and
    # the passes use it. It is not, on its own, a correct description of
    # LIVENESS here, and substituting it silently miscompiled four programs
    # (r39_running_average's running sum stuck at its first value,
    # 382_nested_listcomp, 425_generator_pipeline, 999_comprehensive_codegen).
    # Replacing it again needs a full differential run, not a case-by-case fix:
    # every variant tried so far trades one set of programs for another --
    # tightening the range frees a register that is still live, and widening it
    # exhausts the pool and crashes the "GP result expected" path instead.
    regions = try_regions_resolved(func)

    def _in_try_region(idx: int) -> bool:
        return any(idx in members for _setjmp, members in regions)

    label_to_idx = {b.label: bi for bi, b in enumerate(func.blocks)}
    loop_start = list(range(len(func.blocks)))
    loop_end = list(range(len(func.blocks)))
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.op not in ("br", "br.t"):
                continue
            targets = instr.operands[1:] if instr.op == "br.t" else instr.operands
            for target in targets:
                ti = label_to_idx.get(str(target))
                if ti is None or ti > bi or _in_try_region(ti):
                    continue
                for k in range(ti, bi + 1):
                    if loop_start[k] > ti:
                        loop_start[k] = ti
                    if loop_end[k] < bi:
                        loop_end[k] = bi

    for name, (bi, _ii) in list(last.items()):
        start, end = loop_start[bi], loop_end[bi]
        if end >= bi and def_block.get(name, bi) < start:
            last[name] = (end, len(func.blocks[end].instrs))

    try_regions = try_regions_resolved(func)
    if try_regions:
        region_referenced: dict[int, set[str]] = {}
        for bi, block in enumerate(func.blocks):
            for instr in block.instrs:
                for op in instr.operands:
                    if not hasattr(op, "name"):
                        continue
                    for ri, (_setjmp_bi, members) in enumerate(try_regions):
                        if bi in members:
                            region_referenced.setdefault(ri, set()).add(op.name)
        for name, (bi, _ii) in list(last.items()):
            db = def_block.get(name, bi)
            for ri, (setjmp_bi, members) in enumerate(try_regions):
                end_bi = max(members)
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
    """Belady: evict the value used furthest in the future (or already
    dead). Ported verbatim from x86_64/regalloc.py -- see that module's
    docstring for why `<` (not `<=`) against `now` matters."""
    _INF = (10**9, 10**9)
    return max(in_reg, key=lambda n: _INF if last_use.get(n, (-1, -1)) < now else last_use[n])


def _compute_crosses_call(func: Any) -> set[str]:
    """Names of values whose live range spans a `call` instruction.
    Ported verbatim from x86_64/regalloc.py -- AArch64's AAPCS64 has the
    identical caller-saved/callee-saved hazard (a caller-saved-register
    value is not safe to read after a `bl`/`blr`), so the same
    conservative by-block analysis applies unchanged."""
    def_pos: dict[str, tuple[int, int]] = {}
    use_positions: dict[str, list[tuple[int, int]]] = {}
    call_positions: list[tuple[int, int]] = []

    for param in func.params:
        def_pos[param.name] = (0, -1)

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

def allocate(func: Any, abi: str = "aapcs64") -> AllocResult:
    """
    Allocate registers for *func* (an IRFunc duck-type).

    abi: "aapcs64" -- the only ABI this backend targets initially (shared
         by Linux and Windows ARM64 argument-register assignment, unlike
         x86-64's genuine SysV/Win64 split -- see this module's docstring).
         Accepted as a parameter (rather than hardcoded) purely to mirror
         x86_64/regalloc.py's call signature for driver.py wiring
         consistency, and to leave room for a real Windows-ARM64-specific
         variant later without changing this function's shape again.
    """
    int_args   = ARG_REGS
    fp_args    = FP_ARG_REGS
    callee_gp  = set(CALLEE_SAVED)
    callee_fp  = set(CALLEE_SAVED_FP)

    locs:            dict[str, Location] = {}
    alloca_slots:    dict[str, int]      = {}
    used_callee_gp:  set[Reg]            = set()
    used_callee_fp:  set[VReg]           = set()
    stack_top = 0  # bytes consumed below FP so far

    free_gp: list[Reg]  = list(_GP_POOL)
    free_fp: list[VReg] = list(_FP_POOL)

    in_gp: dict[str, Reg]  = {}  # values currently in a GP register
    in_fp: dict[str, VReg] = {}  # values currently in a D-register

    last_use = _last_uses(func)
    crosses_call = _compute_crosses_call(func)
    now: tuple[int, int] = (-1, -1)

    # ── inner helpers that close over the mutable state ───────────────────────

    def _take_gp(prefer_callee_saved: bool = False) -> Reg:
        nonlocal stack_top
        if prefer_callee_saved:
            # A value crossing a call must not land in a caller-saved
            # register -- see x86_64/regalloc.py's identical guard for the
            # real crash class this prevents (a call-crossing value
            # silently clobbered by the callee). Same hard-exclusion
            # shape: evict a callee-saved HOLDER rather than falling
            # through to a caller-saved register if none is free.
            for i, r in enumerate(free_gp):
                if r in callee_gp:
                    return free_gp.pop(i)
            if free_gp:
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
        if free_gp:
            return free_gp.pop(0)
        victim = _pick_evict(in_gp, last_use, now)
        stack_top += 8
        locs[victim] = StackLoc(-stack_top)
        freed = in_gp.pop(victim)
        free_gp.append(freed)
        return free_gp.pop(0)

    def _take_fp() -> VReg:
        nonlocal stack_top
        if free_fp:
            return free_fp.pop(0)
        victim = _pick_evict(in_fp, last_use, now)
        stack_top += 8
        locs[victim] = StackLoc(-stack_top)
        freed = in_fp.pop(victim)
        free_fp.append(freed)
        return free_fp.pop(0)

    def _alloc_gp(name: str) -> None:
        r = _take_gp(prefer_callee_saved=name in crosses_call)
        locs[name] = RegLoc(r)
        in_gp[name] = r
        if r in callee_gp:
            used_callee_gp.add(r)

    def _alloc_fp(name: str) -> None:
        v = _take_fp()
        locs[name] = VLoc(v)
        in_fp[name] = v
        if v in callee_fp:
            used_callee_fp.add(v)

    def _free_if_dead(op: Any, pos: tuple[int, int]) -> None:
        if not hasattr(op, "name"):
            return
        n = op.name
        if last_use.get(n) != pos:
            return
        if n in in_gp:
            free_gp.append(in_gp.pop(n))
        elif n in in_fp:
            free_fp.append(in_fp.pop(n))

    # ── Assign function parameters to ABI entry locations (AAPCS64: one
    # shared 8-slot int counter, one shared 8-slot FP counter -- both
    # Linux and Windows ARM64 use this same assignment, unlike x86-64's
    # genuine SysV/Win64 split) ────────────────────────────────────────────

    stack_params: list[str] = []
    int_i = fp_i = 0
    for param in func.params:
        if _is_float(param.type.name):
            if fp_i < len(fp_args):
                v = fp_args[fp_i]; fp_i += 1
                locs[param.name] = VLoc(v)
                in_fp[param.name] = v
                if v in free_fp:
                    free_fp.remove(v)
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
                    size = int(instr.operands[0]) if instr.operands else 8
                    size = (size + 7) & ~7
                    stack_top += size
                    alloca_slots[result.name] = -stack_top
                elif instr.op == "call" and result.name in crosses_call:
                    stack_top += _slot_size(result.type.name)
                    locs[result.name] = StackLoc(-stack_top)
                elif _is_float(result.type.name):
                    _alloc_fp(result.name)
                else:
                    _alloc_gp(result.name)

            for op in instr.operands:
                _free_if_dead(op, now)

    # ── Align total stack frame to 16 bytes (AAPCS64 6.2.2: SP must be
    # 16-byte aligned at every public function call boundary). The
    # prologue always saves the [FP, LR] pair as one STP (16 bytes, even
    # parity by construction, unlike x86-64's push-per-register prologue
    # whose parity depends on how many callee-saved GP registers this
    # specific function happens to use) -- so, unlike the x86-64 module's
    # `target_residue` calculation, stack_top's own residue target is
    # always a flat 0 here, with no callee-saved-count-dependent branch
    # needed.
    if stack_top % 16 != 0:
        stack_top += 16 - (stack_top % 16)

    # Incoming stack-passed arguments sit above the saved [FP, LR] frame
    # record (16 bytes) at a positive offset from FP.
    incoming_stack_base = 16
    for i, name in enumerate(stack_params):
        locs[name] = StackLoc(incoming_stack_base + 8 * i)

    return AllocResult(
        locs             = locs,
        alloca_slots     = alloca_slots,
        stack_bytes      = stack_top,
        callee_saved     = sorted(used_callee_gp, key=int),
        callee_saved_fp  = sorted(used_callee_fp, key=int),
    )
