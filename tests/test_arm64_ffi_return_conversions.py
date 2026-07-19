from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_float_ceil import _CEIL_SOURCE
from asmpython._backends.arm64._verify_float_floor import _FLOOR_SOURCE
from asmpython._backends.arm64._verify_float_trunc import _TRUNC_SOURCE
from asmpython._backends.arm64.source_build import lower_source


class Arm64FfiReturnConversionTests(unittest.TestCase):
    def _assert_f2i_calls(self, source: str, symbol: str) -> None:
        module = lower_source(source)
        matches = []
        for func in module.funcs:
            for block in func.blocks:
                for index, instr in enumerate(block.instrs):
                    if (
                        instr.op == "call"
                        and instr.operands
                        and instr.operands[0] == symbol
                    ):
                        matches.append((block, index, instr))

        self.assertGreater(len(matches), 0)
        for block, index, call in matches:
            self.assertIsNotNone(call.result)
            self.assertEqual(call.result.type.name, "f64")
            self.assertLess(index + 1, len(block.instrs))

            conversion = block.instrs[index + 1]
            self.assertEqual(conversion.op, "fptosi")
            self.assertIsNotNone(conversion.result)
            self.assertEqual(conversion.result.type.name, "i64")
            self.assertEqual(conversion.operands, [call.result])

    def test_ceil_uses_explicit_f2i_return_conversion(self) -> None:
        self._assert_f2i_calls(_CEIL_SOURCE, "ceil")

    def test_floor_uses_explicit_f2i_return_conversion(self) -> None:
        self._assert_f2i_calls(_FLOOR_SOURCE, "floor")

    def test_trunc_uses_explicit_f2i_return_conversion(self) -> None:
        self._assert_f2i_calls(_TRUNC_SOURCE, "trunc")


if __name__ == "__main__":
    unittest.main()
