"""
Linear-scan register allocator for the x86-64 backend.

Maps every IRValue in an IRFunc to either a physical register or an
RBP-relative stack slot using Belady's optimal eviction policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

from ..._compiler.ssa.cfg import successor_indices, try_regions_resolved
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
    # Parameters that outlive a call and so cannot stay in their (caller-saved)
    # incoming ABI argument register: the prologue must copy each one into the
    # given RBP-relative slot, which `locs` already points at. Entries are
    # (incoming_register, rbp_offset, ir_type_name); the register is a Reg for
    # integer/pointer parameters and an XmmReg for float ones.
    param_spills:     list[tuple[object, int, str]] = field(default_factory=list)


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


# Liveness, loop structure, try-region extension and eviction choice live in
# _backends/_common/liveness.py: they are pure IR analysis with nothing
# x86-specific in them, and arm64 needs the identical reasoning. They were
# duplicated once; a fix to one copy did not reach the other. See that module.
from .._common.liveness import (  # noqa: F401  (re-exported for tests)
    block_liveness as _block_liveness,
    last_uses as _last_uses,
    live_before_definition as _live_before_definition,
    pick_evict as _pick_evict,
)


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
    loop_carried = _live_before_definition(func)
    crosses_call = _compute_crosses_call(func)
    crosses_var_shift = _compute_crosses_var_shift(func)
    now: tuple[int, int] = (-1, -1)  # updated each instruction

    # ── inner helpers that close over the mutable state ───────────────────────

    def _take_gp(
        prefer_callee_saved: bool = False,
        avoid_rcx: bool = False,
        require_callee_saved: bool = False,
    ) -> "Reg | None":
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
            # Evict a callee-saved HOLDER to stack and take its register. Only a
            # holder OF a callee-saved register helps: evicting a caller-saved
            # holder would just hand back another register the callee may clobber.
            callee_holders = {n: r for n, r in in_gp.items() if r in callee_gp}
            if callee_holders:
                victim = _pick_evict(callee_holders, last_use, now)
                stack_top += 8
                locs[victim] = StackLoc(-stack_top)
                freed = in_gp.pop(victim)
                free_gp.append(freed)
                for i, r in enumerate(free_gp):
                    if r in callee_gp:
                        return free_gp.pop(i)
            if require_callee_saved:
                # No callee-saved register is obtainable. Falling through here
                # would hand a call-crossing value a caller-saved register that
                # the next `call` clobbers -- the exact silent miscompile this
                # branch exists to prevent (the old `or in_gp` eviction could
                # also free a caller-saved register and then fall through).
                # Signal "no safe register" so `_alloc_gp` homes the value on
                # the stack, which always survives a call.
                return None
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
        nonlocal stack_top
        if name in loop_carried:
            # Live before its own definition in list order -- see
            # _live_before_definition. No register can be reserved that early.
            stack_top += 8
            locs[name] = StackLoc(-stack_top)
            return
        crosses = name in crosses_call
        r = _take_gp(
            prefer_callee_saved=crosses,
            avoid_rcx=name in crosses_var_shift,
            require_callee_saved=crosses,
        )
        if r is None:
            # Nothing callee-saved available for a call-crossing value: home it
            # on the stack. A caller-frame slot survives any call.
            stack_top += 8
            locs[name] = StackLoc(-stack_top)
            return
        locs[name] = RegLoc(r)
        in_gp[name] = r
        if r in callee_gp:
            used_callee_gp.add(r)

    def _alloc_xmm(name: str) -> None:
        nonlocal stack_top
        if name in loop_carried:
            stack_top += _slot_size("f64")
            locs[name] = StackLoc(-stack_top)
            return
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
    param_spills: list[tuple[object, int, str]] = []

    def _home_param(name: str, reg: object, type_name: str) -> bool:
        """Home a call-crossing parameter on the stack instead of its arg register.

        Incoming ABI argument registers (RCX/RDX/... , RDI/RSI/... , XMM0-3/0-7)
        are ALL caller-saved, so a parameter still live after a `call` is
        clobbered by the callee. Every other call-crossing value is protected
        (`_alloc_gp`'s callee-saved preference, or the stack-homing branch in the
        instruction walk), but a parameter is pinned to its entry location and
        never consulted `crosses_call` -- silently miscompiling any function that
        uses a parameter after calling something.

        Latent while the frontend stored every parameter into a stack slot
        immediately (the parameter died in the entry block); reachable as soon as
        an optimization pass (mem2reg) promotes those slots to SSA values.
        Returns True if the parameter was homed on the stack.
        """
        nonlocal stack_top
        if name not in crosses_call:
            return False
        stack_top += _slot_size(type_name)
        locs[name] = StackLoc(-stack_top)
        param_spills.append((reg, -stack_top, type_name))
        return True

    int_i = xmm_i = 0
    for arg_i, param in enumerate(func.params):
        if _is_float(param.type.name):
            if abi == "win64":
                if arg_i < len(xmm_args):
                    x = xmm_args[arg_i]
                    if not _home_param(param.name, x, param.type.name):
                        locs[param.name] = XmmLoc(x)
                        in_xmm[param.name] = x
                        if x in free_xmm:
                            free_xmm.remove(x)
                else:
                    stack_params.append(param.name)
            else:
                if xmm_i < len(xmm_args):
                    x = xmm_args[xmm_i]; xmm_i += 1
                    if not _home_param(param.name, x, param.type.name):
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
                    if not _home_param(param.name, r, param.type.name):
                        locs[param.name] = RegLoc(r)
                        in_gp[param.name] = r
                        if r in free_gp:
                            free_gp.remove(r)
                else:
                    stack_params.append(param.name)
            else:
                if int_i < len(int_args):
                    r = int_args[int_i]; int_i += 1
                    if not _home_param(param.name, r, param.type.name):
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
                elif result.name in crosses_call and (
                    instr.op == "call"
                    or result.type.name in ("f32", "f64")
                ):
                    # A value live across a call cannot stay in a caller-saved
                    # register -- the callee is free to clobber it. Two result
                    # classes are homed on the stack here rather than in a
                    # register:
                    #   * ANY call result (it comes back in a caller-saved reg,
                    #     RAX / XMM0), and
                    #   * ANY SCALAR float value (f32/f64), because scalar floats
                    #     have no dependable callee-saved home across ABIs: SysV
                    #     has NO callee-saved XMM registers at all, and while
                    #     Win64 nominally preserves XMM6-15, this backend's
                    #     prologue/epilogue don't save them -- so a float parked
                    #     in a caller-saved XMM across a call was silently
                    #     clobbered. Observed as `round(0.362, 2)` returning
                    #     0.0 / 100.0 nondeterministically: the `x` operand (a
                    #     `const`, not a call result, so it missed the
                    #     call-result case above) was destroyed by the internal
                    #     `pow(10, ndigits)` call before the `x * scale` multiply
                    #     could read it.
                    # v128 (SIMD) values are deliberately NOT included: they need
                    # a 16-byte spill/reload (movdqu), whereas the scalar spill
                    # path homes them in an 8-byte slot -- and a packed vector
                    # living across a call is rare enough that it keeps its
                    # prior (register) treatment rather than widening this path.
                    # Non-call GP values that cross a call are handled by
                    # `_alloc_gp`'s `prefer_callee_saved` path instead (a
                    # callee-saved GP register survives the call for free).
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
        param_spills     = param_spills,
    )
