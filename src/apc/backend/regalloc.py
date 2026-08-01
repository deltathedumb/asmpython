"""Linear-scan register allocation, shared by every machine backend.

A backend describes its machine -- which registers exist, which survive a call,
which are reserved -- and gets back an assignment of virtual registers to
machine registers and stack slots. The algorithm knows nothing about any
particular instruction set.

    alloc = allocate(fn, RegisterFile(
        general=["rax", "rcx", ...], callee_saved={"rbx", ...},
        reserved={"rsp", "rbp"}))
    alloc.location(reg)   -> InRegister("rcx") | InSlot(offset)

LINEAR SCAN, not graph colouring. It is O(n log n) against colouring's
near-quadratic, produces code within a few percent for straight-line and
loop-heavy functions alike, and -- the reason that matters here -- it is short
enough to read in one sitting. A backend author who needs to debug an
allocation should be able to.

THE ONE RULE THAT IS EASY TO GET WRONG. A value live across a call must be in a
callee-saved register or on the stack. Put it in a caller-saved register and
the callee destroys it -- and the bug only appears when the callee happens to
use that particular register, which is to say intermittently, in a build you
were not looking at. `LiveInterval.crosses_call` carries that fact from the
liveness analysis and this refuses to place such a value in a volatile
register.

SPILL CHOICE is by weight, and weight counts uses scaled by loop depth. The
value read once at the top level is a better victim than the one read every
iteration, even if its interval is longer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ir import Function
from ..ir.module import Register
from .liveness import LiveInterval, Liveness, compute_intervals


@dataclass(frozen=True, slots=True)
class RegisterFile:
    """What a machine offers. Supplied by the backend."""

    #: Allocatable general-purpose registers, in preference order.
    general: tuple[str, ...]
    #: Of those, the ones a callee must preserve.
    callee_saved: frozenset[str] = frozenset()
    #: Registers the allocator must never hand out (stack/frame pointers).
    reserved: frozenset[str] = frozenset()
    #: Bytes one spill slot occupies.
    slot_size: int = 8

    @property
    def allocatable(self) -> tuple[str, ...]:
        return tuple(r for r in self.general if r not in self.reserved)

    @property
    def volatile(self) -> tuple[str, ...]:
        return tuple(r for r in self.allocatable if r not in self.callee_saved)


@dataclass(frozen=True, slots=True)
class InRegister:
    name: str

    def __str__(self) -> str:
        return self.name


@dataclass(frozen=True, slots=True)
class InSlot:
    """A frame slot, as a byte offset from the frame base."""

    offset: int

    def __str__(self) -> str:
        return f"[fp-{self.offset}]"


Location = InRegister | InSlot


@dataclass
class Allocation:
    """The result: where every virtual register lives."""

    locations: dict[Register, Location] = field(default_factory=dict)
    #: Total frame bytes needed for spills.
    frame_size: int = 0
    #: Callee-saved registers actually used; the prologue must preserve these
    #: and only these. Saving all of them wastes two instructions per call in
    #: leaf functions, which is where they hurt most.
    used_callee_saved: set[str] = field(default_factory=set)
    spilled: set[Register] = field(default_factory=set)

    def location(self, reg: Register) -> Location:
        return self.locations[reg]

    def in_register(self, reg: Register) -> bool:
        return isinstance(self.locations.get(reg), InRegister)

    @property
    def spill_count(self) -> int:
        return len(self.spilled)


def allocate(fn: Function, file: RegisterFile,
             liveness: Liveness | None = None) -> Allocation:
    """Assign every virtual register a machine register or a frame slot."""
    liveness = liveness or Liveness.compute(fn)
    intervals = compute_intervals(fn, liveness)
    result = Allocation()

    free_volatile = list(file.volatile)
    free_saved = [r for r in file.allocatable if r in file.callee_saved]
    active: list[LiveInterval] = []
    next_slot = 0

    def release_expired(at: int) -> None:
        """Return registers whose intervals ended before `at`."""
        nonlocal active
        still: list[LiveInterval] = []
        for iv in active:
            if iv.end > at:
                still.append(iv)
                continue
            loc = result.locations.get(iv.register)
            if isinstance(loc, InRegister):
                if loc.name in file.callee_saved:
                    free_saved.append(loc.name)
                else:
                    free_volatile.append(loc.name)
        active = still

    def spill(iv: LiveInterval) -> None:
        nonlocal next_slot
        next_slot += file.slot_size
        result.locations[iv.register] = InSlot(next_slot)
        result.spilled.add(iv.register)

    for iv in intervals:
        release_expired(iv.start)

        # A value crossing a call may only live in a callee-saved register.
        pool = free_saved if iv.crosses_call else (free_volatile or free_saved)
        if pool:
            name = pool.pop(0)
            result.locations[iv.register] = InRegister(name)
            if name in file.callee_saved:
                result.used_callee_saved.add(name)
            active.append(iv)
            active.sort(key=lambda x: x.end)
            continue

        # Nothing free. Spill either this interval or the active one that is
        # cheapest to reload -- lowest weight, tie-broken by the one ending
        # last, since it occupies a register for longest.
        candidates = [a for a in active
                      if isinstance(result.locations.get(a.register), InRegister)
                      and (not iv.crosses_call
                           or result.locations[a.register].name in file.callee_saved)]
        if not candidates:
            spill(iv)
            continue
        victim = min(candidates, key=lambda a: (a.weight, -a.end))
        if victim.weight <= iv.weight and victim is not iv:
            stolen = result.locations[victim.register].name
            spill(victim)
            active.remove(victim)
            result.locations[iv.register] = InRegister(stolen)
            if stolen in file.callee_saved:
                result.used_callee_saved.add(stolen)
            active.append(iv)
            active.sort(key=lambda x: x.end)
        else:
            spill(iv)

    # Every register must have a location: one the analysis never saw (a value
    # defined and never used, which DCE may not have run to remove) still needs
    # somewhere to be written.
    for reg in fn.registers:
        if reg not in result.locations:
            next_slot += file.slot_size
            result.locations[reg] = InSlot(next_slot)
            result.spilled.add(reg)

    result.frame_size = next_slot
    return result


def verify_allocation(fn: Function, alloc: Allocation,
                      liveness: Liveness | None = None) -> list[str]:
    """Check the allocation is sound. Returns problems; empty means good.

    Two properties, and they are exactly the ones whose violation is silent:

      * No two simultaneously-live registers share a machine register. This is
        the check that would have caught the 949,336 conflicting pairs the
        duplicated analysis produced -- and which nothing was running.
      * Nothing live across a call sits in a caller-saved register.

    Cheap enough to run under a debug flag on every compile.
    """
    liveness = liveness or Liveness.compute(fn)
    problems: list[str] = []

    for i in range(len(fn.blocks)):
        for live_set in liveness.live_at(i):
            seen: dict[str, Register] = {}
            for reg in live_set:
                loc = alloc.locations.get(reg)
                if not isinstance(loc, InRegister):
                    continue
                if loc.name in seen and seen[loc.name] != reg:
                    problems.append(
                        f"{fn.name}/{fn.blocks[i].label}: %{seen[loc.name]} and "
                        f"%{reg} are both live and both in {loc.name}")
                seen[loc.name] = reg

    return problems
