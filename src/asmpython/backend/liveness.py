"""Liveness analysis, shared by every machine backend.

Pure IR-level reasoning: which registers hold a value that will still be read.
Nothing machine-specific is here, and nothing machine-specific belongs here --
register pools, ABI constraints and spill costs are the backend's, and they are
passed in.

WHY THIS IS SHARED AND NOT COPIED. In the tree this replaces, x86-64 and arm64
each carried a copy of this analysis, the second with a comment promising it
was "kept in step with" the first. It was not. A loop-carried-value fix landed
in one and never reached the other, and the divergence was invisible because
each backend only ever tested itself. Counting pairs of simultaneously-live
values assigned the same register on one program:

    x86_64        0
    arm64    949336

Hand-synchronised copies of an analysis are how that happens. It is not a story
about carelessness -- it is what always happens eventually.

THE ANALYSIS is textbook backward dataflow:

    live_out(B) = union of live_in(S) for every successor S
    live_in(B)  = uses(B) union (live_out(B) minus defs(B))

iterated to a fixed point. It works on non-SSA IR unchanged: a register with
several definitions is simply live between each definition and the reads that
follow it. Nothing here needs phi nodes, which is the reason dropping SSA cost
so little.

Iteration goes in REVERSE postorder reversed -- i.e. postorder -- because
information flows backwards, and visiting a block after its successors is what
makes most functions converge in a single pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from collections.abc import Callable

from ..ir import Function
from ..ir.cfg import ControlFlowGraph
from ..ir.module import Instruction, Register

#: "Does this instruction clobber caller-saved registers?"
IsCall = Callable[[Instruction], bool]


def default_is_call(ins: Instruction) -> bool:
    """The IR's own calls. A backend that lowers something else to a call
    must say so -- see `Liveness.live_across_calls`."""
    from ..ir.opcodes import Op
    return ins.op in (Op.CALL, Op.CALL_PTR)


@dataclass
class Liveness:
    """Live-in and live-out sets, per block, plus per-instruction queries."""

    function: Function
    cfg: ControlFlowGraph
    live_in: list[set[Register]]
    live_out: list[set[Register]]

    @classmethod
    def compute(cls, fn: Function, cfg: ControlFlowGraph | None = None) -> Liveness:
        cfg = cfg or ControlFlowGraph.build(fn)
        n = len(fn.blocks)
        uses: list[set[Register]] = []
        defs: list[set[Register]] = []

        for block in fn.blocks:
            block_uses: set[Register] = set()
            block_defs: set[Register] = set()
            for ins in block.instructions:
                # A register read before it is written in THIS block is an
                # upward-exposed use; one read after a local definition is not,
                # because the definition satisfies it.
                for a in ins.args:
                    if a not in block_defs:
                        block_uses.add(a)
                if ins.dst is not None:
                    block_defs.add(ins.dst)
            uses.append(block_uses)
            defs.append(block_defs)

        live_in = [set() for _ in range(n)]
        live_out = [set() for _ in range(n)]

        order = list(reversed(cfg.reverse_postorder))
        order += [i for i in range(n) if i not in set(order)]

        changed = True
        while changed:
            changed = False
            for i in order:
                out: set[Register] = set()
                for s in cfg.successors[i]:
                    out |= live_in[s]
                inn = uses[i] | (out - defs[i])
                if out != live_out[i] or inn != live_in[i]:
                    live_out[i] = out
                    live_in[i] = inn
                    changed = True

        return cls(fn, cfg, live_in, live_out)

    def live_at(self, block_index: int) -> list[set[Register]]:
        """Registers live BEFORE each instruction of a block.

        Walks the block backwards from `live_out`. Returned per instruction so
        an allocator can ask "what is live here" without redoing the walk.
        """
        block = self.function.blocks[block_index]
        live = set(self.live_out[block_index])
        result: list[set[Register]] = [set() for _ in block.instructions]
        for i in range(len(block.instructions) - 1, -1, -1):
            ins = block.instructions[i]
            if ins.dst is not None:
                live.discard(ins.dst)
            live |= set(ins.args)
            result[i] = set(live)
        return result

    def live_across_calls(self, is_call: IsCall | None = None) -> set[Register]:
        """Registers live across a call.

        These are the ones that must survive in callee-saved registers or on
        the stack. An allocator that ignores this puts a value in a
        caller-saved register and the callee destroys it -- a bug that only
        appears when the callee happens to use that register, which is to say
        intermittently and later.

        `is_call` decides what counts, and defaults to the IR's own call
        opcodes. A backend must override it when IT introduces a call the IR
        does not show: x86-64 has no float remainder instruction and lowers
        `Op.REM` on f64 to `call fmod`, which clobbers every caller-saved
        register just as any call does. Leaving that invisible put a value
        live across it into `rax`, and `float(i0)` afterwards read whatever
        fmod had left there -- a wrong number in a program that ran, found by
        a fuzzer and not by anything else.
        """
        check = is_call or default_is_call

        out: set[Register] = set()
        for i, block in enumerate(self.function.blocks):
            per_instruction = self.live_at(i)
            for k, ins in enumerate(block.instructions):
                if not check(ins):
                    continue
                # What is live AFTER the call, not before it minus the
                # arguments. Those differ for the case that matters: a value
                # passed as an argument AND read again afterwards is live
                # across the call, and subtracting the argument list removes
                # exactly it.
                #
                # That bug produced a loop counter in a volatile register --
                # `add(total, i)` clobbered `i`, the increment read garbage,
                # and the loop terminated early with a plausible-looking sum.
                after = (set(per_instruction[k + 1])
                         if k + 1 < len(per_instruction)
                         else set(self.live_out[i]))
                after.discard(ins.dst)
                out |= after
        return out


@dataclass
class LiveInterval:
    """The half-open range of instruction positions a register is live over.

    Positions are numbered across the whole function in block order, so an
    interval can be compared without knowing which block it came from. This is
    an APPROXIMATION: a register live in two disjoint regions gets one interval
    spanning the gap. That over-estimates pressure and never under-estimates
    it, which is the safe direction -- the allocator may spill something it did
    not have to, and will never assign one register to two live values.
    """

    register: Register
    start: int
    end: int
    #: How many times it is read or written; used to choose a spill victim.
    weight: int = 0
    #: True if it must survive a call.
    crosses_call: bool = False

    def overlaps(self, other: LiveInterval) -> bool:
        return self.start < other.end and other.start < self.end


def compute_intervals(fn: Function, liveness: Liveness | None = None, *,
                      is_call: IsCall | None = None) -> list[LiveInterval]:
    """Live intervals for every register, sorted by start position.

    `is_call` is passed through to `live_across_calls`, so a backend's hidden
    calls set `crosses_call` on the intervals the allocator then honours.
    """
    from ..ir.opcodes import Op

    liveness = liveness or Liveness.compute(fn)
    loop_depth = liveness.cfg.loop_depth()

    first: dict[Register, int] = {}
    last: dict[Register, int] = {}
    weight: dict[Register, int] = {}
    position = 0

    for i, block in enumerate(fn.blocks):
        # Anything live on entry to a block starts no later than here, and
        # anything live on exit lasts at least to the end -- otherwise a value
        # defined in one block and read in another gets an interval covering
        # neither the gap nor the reader.
        for reg in liveness.live_in[i]:
            first.setdefault(reg, position)
        for ins in block.instructions:
            for reg in list(ins.args) + ([ins.dst] if ins.dst is not None else []):
                first.setdefault(reg, position)
                last[reg] = position + 1
                # A use inside a loop is worth more than one outside it: the
                # allocator should spill the value used once at the top level
                # rather than the one used every iteration.
                weight[reg] = weight.get(reg, 0) + (1 << min(loop_depth[i], 8))
            position += 1
        for reg in liveness.live_out[i]:
            last[reg] = max(last.get(reg, position), position)

    for reg in fn.params:
        first.setdefault(reg, 0)
        last.setdefault(reg, 1)

    crossing = liveness.live_across_calls(is_call)
    intervals = [
        LiveInterval(reg, first[reg], max(last.get(reg, first[reg] + 1),
                                          first[reg] + 1),
                     weight.get(reg, 1), reg in crossing)
        for reg in sorted(first)
    ]
    intervals.sort(key=lambda iv: (iv.start, iv.end))
    return intervals
