from __future__ import annotations

import unittest

from asmpython._compiler import ir_lower
from asmpython._compiler.errors import SemaError
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze


def _lower(source: str):
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    sema_analyze(module)
    return ir_lower.lower_module(module)


def _instructions(module):
    return [
        instr
        for func in module.funcs
        for block in func.blocks
        for instr in block.instrs
    ]


class TupleIterationLoweringTests(unittest.TestCase):
    def test_homogeneous_int_tuple_uses_list_header_iteration(self) -> None:
        lowered = _lower(
            "nums = (0, 1, 2)\n"
            "total = 0\n"
            "for value in nums:\n"
            "    total += value\n"
        )
        instructions = _instructions(lowered)
        self.assertTrue(any(instr.op == "gep" and instr.operands[-1] == 8 for instr in instructions))
        self.assertTrue(any(instr.op == "gep" and instr.operands[-1] == 16 for instr in instructions))

    def test_homogeneous_float_tuple_loads_float_cells(self) -> None:
        lowered = _lower(
            "nums = (1.5, 2.5)\n"
            "total = 0.0\n"
            "for value in nums:\n"
            "    total += value\n"
        )
        loads = [
            instr
            for instr in _instructions(lowered)
            if instr.op == "load" and instr.result is not None
        ]
        self.assertTrue(any(instr.result.type.name == "f64" for instr in loads))

    def test_heterogeneous_tuple_iteration_remains_rejected(self) -> None:
        with self.assertRaisesRegex(SemaError, "heterogeneous tuple"):
            _lower(
                "items = (1, \"two\")\n"
                "for item in items:\n"
                "    print(item)\n"
            )


if __name__ == "__main__":
    unittest.main()
