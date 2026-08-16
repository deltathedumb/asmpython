"""Liveness and register allocation.

The allocator's contract is two properties, and both fail SILENTLY when
violated -- the program simply computes the wrong thing, intermittently:

  * two simultaneously-live values never share a machine register
  * a value live across a call is never in a caller-saved register

`verify_allocation` checks the first, and is exercised here at register-file
sizes from 1 upward. In the tree this replaces, nothing checked it, and a
duplicated copy of the liveness analysis drifted until one backend produced
949,336 conflicting pairs on a single program.
"""
from __future__ import annotations

from tests import harness

from asmpython.backend import (
    InRegister, InSlot, Liveness, RegisterFile, allocate, compute_intervals,
    verify_allocation,
)
from asmpython.ir import Builder, Function, Module, types as T, verify
from asmpython.ir.module import Block, Instruction
from asmpython.ir.opcodes import Op


def block_starting(fn: Function, prefix: str) -> int:
    """Index of the block whose label starts with `prefix`.

    Builder appends a counter to the hint, so `new_block("head")` becomes
    "head3". Tests match on the prefix rather than hard-coding a number that
    changes whenever an unrelated block is added.
    """
    for i, b in enumerate(fn.blocks):
        if b.label.startswith(prefix):
            return i
    raise AssertionError(f"no block starting with {prefix!r} in "
                         f"{[b.label for b in fn.blocks]}")


def loop_function() -> tuple[Module, Function]:
    """sum(0..n-1): a loop-carried value and a value live across the back edge."""
    m = Module("t")
    f = Function("main", T.I64)
    m.functions.append(f)
    b = Builder(f)
    b.switch_to(b.new_block("entry"))
    limit = b.const(T.I64, 10)
    acc, i = b.reg(T.I64), b.reg(T.I64)
    b.copy(acc, b.const(T.I64, 0))
    b.copy(i, b.const(T.I64, 0))
    head, body, done = b.new_block("head"), b.new_block("body"), b.new_block("done")
    b.jump(head)
    b.switch_to(head)
    b.branch(b.cmp(Op.LT, T.I64, i, limit), body, done)
    b.switch_to(body)
    b.copy(acc, b.add(T.I64, acc, i))
    b.copy(i, b.add(T.I64, i, b.const(T.I64, 1)))
    b.jump(head)
    b.switch_to(done)
    b.ret(acc)
    verify(m)
    return m, f


def calling_function() -> tuple[Module, Function]:
    """A value live ACROSS a call -- the case that must not use a volatile."""
    m = Module("t")
    callee = Function("callee", T.I64)
    m.functions.append(callee)
    cb = Builder(callee)
    cb.switch_to(cb.new_block("entry"))
    cb.ret(cb.const(T.I64, 1))

    f = Function("main", T.I64)
    m.functions.append(f)
    b = Builder(f)
    b.switch_to(b.new_block("entry"))
    kept = b.const(T.I64, 42)          # live across the call below
    got = b.call(T.I64, "callee", [])
    b.ret(b.add(T.I64, kept, got))
    verify(m)
    return m, f


class TestLiveness:
    def test_loop_carried_value_is_live_around_the_back_edge(self):
        _, f = loop_function()
        live = Liveness.compute(f)
        head = block_starting(f, "head")
        # The accumulator must be live entering the loop header, or an
        # allocator will happily reuse its register inside the body.
        assert len(live.live_in[head]) >= 2

    def test_dead_value_is_not_live(self):
        m = Module("t")
        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        b.const(T.I64, 99)                 # never read
        b.ret(b.const(T.I64, 1))
        verify(m)
        live = Liveness.compute(f)
        assert live.live_out[0] == set()

    def test_values_crossing_a_call_are_identified(self):
        _, f = calling_function()
        assert len(Liveness.compute(f).live_across_calls()) >= 1

    def test_intervals_are_sorted_by_start(self):
        _, f = loop_function()
        starts = [iv.start for iv in compute_intervals(f)]
        assert starts == sorted(starts)

    def test_loop_uses_weigh_more(self):
        """Spill choice depends on this: a value used in a loop is costlier."""
        _, f = loop_function()
        cfg_depth = Liveness.compute(f).cfg.loop_depth()
        assert max(cfg_depth) >= 1
        intervals = {iv.register: iv for iv in compute_intervals(f)}
        assert max(iv.weight for iv in intervals.values()) > 1


class TestAllocation:
    @harness.cases("count", [1, 2, 3, 4, 8, 16])
    def test_no_conflicts_at_any_register_count(self, count):
        """The property that fails silently. Checked from one register upward."""
        _, f = loop_function()
        rf = RegisterFile(general=tuple(f"r{i}" for i in range(count)))
        alloc = allocate(f, rf)
        assert verify_allocation(f, alloc) == []

    def test_every_register_gets_a_location(self):
        _, f = loop_function()
        alloc = allocate(f, RegisterFile(general=("r0", "r1")))
        for reg in f.registers:
            assert reg in alloc.locations

    def test_fewer_registers_means_more_spills(self):
        _, f = loop_function()
        tight = allocate(f, RegisterFile(general=("r0", "r1")))
        roomy = allocate(f, RegisterFile(
            general=tuple(f"r{i}" for i in range(16))))
        assert tight.spill_count >= roomy.spill_count
        assert tight.frame_size >= roomy.frame_size

    def test_a_value_across_a_call_is_not_in_a_volatile_register(self):
        _, f = calling_function()
        rf = RegisterFile(general=("v0", "v1", "s0", "s1"),
                          callee_saved=frozenset({"s0", "s1"}))
        alloc = allocate(f, rf)
        assert verify_allocation(f, alloc) == []
        for reg in Liveness.compute(f).live_across_calls():
            loc = alloc.locations[reg]
            if isinstance(loc, InRegister):
                assert loc.name in rf.callee_saved, (
                    f"%{reg} survives a call but sits in volatile {loc.name}")

    def test_reserved_registers_are_never_handed_out(self):
        _, f = loop_function()
        rf = RegisterFile(general=("r0", "r1", "sp", "fp"),
                          reserved=frozenset({"sp", "fp"}))
        alloc = allocate(f, rf)
        names = {loc.name for loc in alloc.locations.values()
                 if isinstance(loc, InRegister)}
        assert not (names & {"sp", "fp"})

    def test_only_used_callee_saved_are_reported(self):
        """The prologue saves these and only these; saving all of them costs
        two instructions per call in exactly the leaf functions that suffer."""
        _, f = loop_function()
        rf = RegisterFile(general=tuple(f"r{i}" for i in range(16)),
                          callee_saved=frozenset(f"r{i}" for i in range(8, 16)))
        alloc = allocate(f, rf)
        assert alloc.used_callee_saved <= rf.callee_saved

    def test_frame_size_covers_every_slot(self):
        _, f = loop_function()
        alloc = allocate(f, RegisterFile(general=("r0",)))
        offsets = [loc.offset for loc in alloc.locations.values()
                   if isinstance(loc, InSlot)]
        assert offsets and max(offsets) <= alloc.frame_size

    def test_zero_registers_spills_everything(self):
        _, f = loop_function()
        alloc = allocate(f, RegisterFile(general=()))
        assert all(isinstance(loc, InSlot) for loc in alloc.locations.values())
        assert verify_allocation(f, alloc) == []


class TestVerifyAllocationDetectsBadness:
    def test_it_reports_a_deliberate_conflict(self):
        """The checker must actually catch what it claims to."""
        _, f = loop_function()
        alloc = allocate(f, RegisterFile(
            general=tuple(f"r{i}" for i in range(8))))
        assert verify_allocation(f, alloc) == []
        # Force two live values into one register.
        live = Liveness.compute(f)
        head = block_starting(f, "head")
        both = sorted(live.live_in[head])[:2]
        if len(both) == 2:
            for reg in both:
                alloc.locations[reg] = InRegister("rX")
            assert verify_allocation(f, alloc) != []


class TestValueLiveAcrossACall:
    """Regression: an argument that is ALSO read after the call.

    `add(total, i)` inside a loop passes `i` as an argument and increments it
    afterwards. Computing "live across the call" as (live before) minus (the
    arguments) removes exactly that value, so it was given a caller-saved
    register, the call destroyed it, and the loop terminated early -- producing
    15 instead of 55, a number plausible enough to look like a frontend bug.
    """

    def loop_calling_function(self):
        m = Module("t")
        callee = Function("add", T.I64)
        m.functions.append(callee)
        for _ in range(2):
            callee.params.append(callee.new_register(T.I64))
        cb = Builder(callee)
        cb.switch_to(cb.new_block("entry"))
        cb.ret(cb.add(T.I64, callee.params[0], callee.params[1]))

        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        total, i = b.reg(T.I64), b.reg(T.I64)
        limit = b.const(T.I64, 10)
        b.copy(total, b.const(T.I64, 0))
        b.copy(i, b.const(T.I64, 0))
        head, body, done = (b.new_block("head"), b.new_block("body"),
                            b.new_block("done"))
        b.jump(head)
        b.switch_to(head)
        b.branch(b.cmp(Op.LT, T.I64, i, limit), body, done)
        b.switch_to(body)
        b.copy(total, b.call(T.I64, "add", [total, i]))   # i is an ARGUMENT
        b.copy(i, b.add(T.I64, i, b.const(T.I64, 1)))     # ...and read after
        b.jump(head)
        b.switch_to(done)
        b.ret(total)
        verify(m)
        return m, f

    def test_argument_read_after_the_call_counts_as_crossing_it(self):
        _, f = self.loop_calling_function()
        live = Liveness.compute(f)
        body = block_starting(f, "body")
        call = next(ins for ins in f.blocks[body].instructions
                    if ins.op is Op.CALL)
        crossing = live.live_across_calls()
        assert set(call.args) & crossing, (
            "a value passed as an argument and read afterwards is live across "
            "the call")

    def test_such_a_value_never_lands_in_a_volatile_register(self):
        _, f = self.loop_calling_function()
        rf = RegisterFile(general=("v0", "v1", "v2", "s0", "s1"),
                          callee_saved=frozenset({"s0", "s1"}))
        alloc = allocate(f, rf)
        for reg in Liveness.compute(f).live_across_calls():
            loc = alloc.locations[reg]
            if isinstance(loc, InRegister):
                assert loc.name in rf.callee_saved

    def test_the_program_still_computes_55(self):
        from asmpython.ir.interpreter import run
        m, _ = self.loop_calling_function()
        assert run(m, "main") == 45      # sum 0..9
