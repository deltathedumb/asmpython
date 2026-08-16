"""Regression tests for the `sink` pass moving work into loops.

`sink` shortens live ranges by moving a pure instruction into the single block
that uses its result. Left unrestricted it will move a computation from outside
a loop to inside one -- which runs it once per iteration instead of once, and
is exactly what `licm` had just finished undoing.

It was also unsound: the register allocator decides whether a value stays live
across a back edge from where it is DEFINED relative to the loop (see
`regalloc._last_uses`), so relocating a definition across a loop boundary
changes that answer underneath it. `--passes licm,sink` produced a wrong
result -- not a crash -- with the IR verifying clean either way.
"""

from __future__ import annotations

import unittest

from asmpython import _passes
from asmpython._compiler.ssa.cfg import loop_membership
from asmpython._compiler.ssa.ir import IRModule
from asmpython._compiler.ssa.ir_verify import validate_ir
from asmpython._frontends.apc import emit_module, parse

SRC = """
func hot(n: i64, a: i64, b: i64) {
    let acc = 0
    for (i = 0..n) {
        acc = acc + (a * b) + i
    }
    ret acc
}: i64
"""


def build(src: str) -> IRModule:
    return emit_module(parse(src), src)


def loop_blocks(func) -> set[int]:
    membership = loop_membership(func)
    return {i for i, loops in membership.items() if loops}


def block_of(func, value_name: str) -> int | None:
    for i, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.result is not None and instr.result.name == value_name:
                return i
    return None


class SinkIntoLoopTests(unittest.TestCase):
    def _run(self, *names: str):
        mod = build(SRC)
        func = next(f for f in mod.funcs if f.name == "hot")
        before = {
            instr.result.name: bi
            for bi, block in enumerate(func.blocks)
            for instr in block.instrs
            if instr.result is not None
        }
        for name in names:
            _passes.get_pass(name).run(mod)
        return mod, next(f for f in mod.funcs if f.name == "hot"), before

    def test_sink_never_moves_a_definition_into_a_loop(self) -> None:
        mod, func, before = self._run("licm", "sink")
        inside = loop_blocks(func)
        for name, was in before.items():
            now = block_of(func, name)
            if now is None or was in inside:
                continue
            self.assertNotIn(
                now, inside,
                f"{name} was outside the loop (block {was}) and sink moved it "
                f"into it (block {now})",
            )

    def test_licm_then_sink_keeps_the_ir_valid(self) -> None:
        mod, _func, _before = self._run("licm", "sink")
        validate_ir(mod)

    def test_sink_alone_keeps_the_ir_valid(self) -> None:
        mod, _func, _before = self._run("sink")
        validate_ir(mod)

    def test_o2_preset_keeps_the_ir_valid(self) -> None:
        mod = build(SRC)
        for name in _passes.PIPELINES["o2"]:
            _passes.get_pass(name).run(mod)
        validate_ir(mod)


if __name__ == "__main__":
    unittest.main()
