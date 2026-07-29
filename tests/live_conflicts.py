"""Does the register allocator put two genuinely-live values in one register?

The allocator decides when a value dies from ``regalloc._last_uses``, which
records a last use by position in the BLOCK LIST. That is only a live range if
control flow runs straight down that list, and it does not: a value live out of
a block whose consumer sits EARLIER in the list -- any loop header, and every
back-edge copy phi elimination emits -- looks dead exactly where it is still
needed, and its register is handed to something else.

So liveness here is computed independently, by real backward dataflow to a
fixpoint. Checking the allocator against its own ``_last_uses`` is circular and
passes unconditionally; that mistake costs a debugging round, which is why this
file exists rather than a one-off script.

Use it as a number to drive to zero::

    python tests/live_conflicts.py tests/cases/130_starred_unpack.py
    python tests/live_conflicts.py tests/cases/130_starred_unpack.py mem2reg

Measured when this was written: 0 conflicts with no passes, 63192 with
``mem2reg`` -- which is why mem2reg is not in the o1/o2 presets.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from asmpython import _passes
from asmpython._backends.x86_64.regalloc import RegLoc, XmmLoc, allocate
from asmpython._compiler import driver, ir_lower
from asmpython._compiler.cfg import successor_indices


def _value_names(operands) -> list[str]:
    return [o.name for o in operands or []
            if hasattr(o, "name") and hasattr(o, "type")]


def block_liveness(func):
    """(live_in, live_out) per block, by backward dataflow to a fixpoint."""
    succs = successor_indices(func)
    n = len(func.blocks)
    use: list[set[str]] = []
    defined: list[set[str]] = []
    for block in func.blocks:
        u: set[str] = set()
        d: set[str] = set()
        for instr in block.instrs:
            for name in _value_names(instr.operands):
                if name not in d:
                    u.add(name)
            if instr.result is not None:
                d.add(instr.result.name)
        use.append(u)
        defined.append(d)

    live_in = [set() for _ in range(n)]
    live_out = [set() for _ in range(n)]
    changed = True
    while changed:
        changed = False
        for bi in range(n - 1, -1, -1):
            out: set[str] = set()
            for si in succs[bi]:
                out |= live_in[si]
            inn = use[bi] | (out - defined[bi])
            if out != live_out[bi] or inn != live_in[bi]:
                live_out[bi], live_in[bi] = out, inn
                changed = True
    return live_in, live_out


def conflicts(ir_module, abi: str = "win64") -> list[str]:
    """Every point where two simultaneously-live values share a register."""
    found: list[str] = []
    for func in ir_module.funcs:
        alloc = allocate(func, abi)
        reg_of = {n: loc for n, loc in alloc.locs.items()
                  if isinstance(loc, (RegLoc, XmmLoc))}
        if not reg_of:
            continue
        _live_in, live_out = block_liveness(func)

        for bi, block in enumerate(func.blocks):
            live = set(live_out[bi])
            for instr in reversed(block.instrs):     # walk back to build `live`
                if instr.result is not None:
                    live.discard(instr.result.name)
                live |= set(_value_names(instr.operands))

                held: dict[object, str] = {}
                for name in live:
                    loc = reg_of.get(name)
                    if loc is None:
                        continue
                    if loc in held:
                        found.append(
                            f"{func.name}/{block.label}: {name} and {held[loc]} "
                            f"both live in {loc} (at {instr.op})")
                    else:
                        held[loc] = name
    return found


def build_ir(source: str, passes: str | None = None):
    module = driver._compile_program(
        source, source_dir=None, entry_path=None,
        whole_program=True, all_errors=False)
    ir_module = ir_lower.lower_module(module)
    for name in (passes or "").split(","):
        if name:
            _passes.get_pass(name).run(ir_module)
    return ir_module


# A 13-line shrink of tests/cases/130_starred_unpack.py that still diverged
# under `--passes mem2reg` before the allocator fix, printing
# `1 [3, 4, 5] [3, 4, 5]` instead of `1 2 [3, 4, 5]`.
#
# Twelve of the thirteen lines are pure setup: the last two statements alone
# compile correctly. That is the useful part -- the bug is REGISTER-PRESSURE
# dependent, so a reduction that keeps only the failing statement hides it.
LOOP_CARRIED_PRESSURE_CASE = """\
a, *rest = [1, 2, 3, 4]
*init, last = [1, 2, 3, 4]
first, *mid, last2 = [1, 2, 3, 4, 5]
xs = ["a", "b", "c", "d"]
x, *ys = xs
nums = [1, 2]
n, *empty = nums
print(n)
fs = [1.5, 2.5, 3.5, 4.5]
f0, *frest = fs
f1, *fmid, f2 = fs
a2, b2, *c2 = [1, 2, 3, 4, 5]
print(a2, b2, c2)
"""


class ShrunkReproTests(unittest.TestCase):
    """The minimal pressure case, conflict-free with and without promotion."""

    def test_no_conflicts_without_passes(self) -> None:
        self.assertEqual(conflicts(build_ir(LOOP_CARRIED_PRESSURE_CASE))[:5], [])

    def test_no_conflicts_under_mem2reg(self) -> None:
        found = conflicts(build_ir(LOOP_CARRIED_PRESSURE_CASE, "mem2reg"))
        self.assertEqual(found[:5], [], f"{len(found)} conflict(s)")


class NoPassLiveConflictTests(unittest.TestCase):
    """The allocator must be conflict-free on the path that actually ships.

    This is the regression guard: a change to `_last_uses`, to lowering, or to
    block emission that starts handing one register to two live values fails
    here instead of silently producing a wrong answer.
    """

    CASES = ("130_starred_unpack.py", "144_with_context_manager.py",
             "151_collections_module.py")

    def test_default_pipeline_has_no_register_conflicts(self) -> None:
        for name in self.CASES:
            path = REPO / "tests" / "cases" / name
            if not path.is_file():
                self.skipTest(f"{name} not present")
            with self.subTest(case=name):
                found = conflicts(build_ir(path.read_text(encoding="utf-8")))
                self.assertEqual(found[:5], [], f"{len(found)} conflict(s)")


def _main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    passes = sys.argv[2] if len(sys.argv) > 2 else None
    found = conflicts(build_ir(path.read_text(encoding="utf-8"), passes))
    for line in found[:10]:
        print("  " + line)
    print(f"\nlive values sharing a register: {len(found)}  "
          f"(passes={passes or 'none'})")
    return 1 if found else 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        raise SystemExit(_main())
    unittest.main()
