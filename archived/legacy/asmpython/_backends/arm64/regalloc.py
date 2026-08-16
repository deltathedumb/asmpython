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

from ..._compiler.ssa.cfg import try_regions_resolved
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


# Shared with x86-64 (and with RISC-V / MIPS when they arrive): liveness, loop
# structure, the try-region extension, and Belady eviction are pure IR analysis.
#
# This file used to carry its own copy, with a comment saying it was "kept in
# step with x86_64/regalloc.py". It was not: a loop-carried-value fix landed
# there and never arrived here, and nothing could see the drift because each
# backend only tested itself. On tests/cases/130_starred_unpack.py under
# --passes mem2reg, counting simultaneously-live values sharing a register:
# x86_64 scored 0 and this backend scored 949336.
from .._common.liveness import (  # noqa: F401  (re-exported for tests)
    block_liveness as _block_liveness,
    last_uses as _last_uses,
    live_before_definition as _live_before_definition,
    pick_evict as _pick_evict,
)


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
    # Values live before their own definition in block-list order (carried
    # around a back edge) cannot be expressed in a linear-scan model: the
    # register would have to be reserved before the walk reaches the
    # definition. Home them on the stack, which is live for the whole
    # function. Same reasoning and same shared analysis as x86-64.
    loop_carried = _live_before_definition(func)
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
        nonlocal stack_top
        if name in loop_carried:
            stack_top += 8
            locs[name] = StackLoc(-stack_top)
            return
        r = _take_gp(prefer_callee_saved=name in crosses_call)
        locs[name] = RegLoc(r)
        in_gp[name] = r
        if r in callee_gp:
            used_callee_gp.add(r)

    def _alloc_fp(name: str) -> None:
        nonlocal stack_top
        if name in loop_carried:
            stack_top += _slot_size("f64")
            locs[name] = StackLoc(-stack_top)
            return
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
