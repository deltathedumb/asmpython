from __future__ import annotations

import math
import struct
import unittest

from asmpython._backends.arm64._verify_float_isfinite import _EXPECTED_REQUIREMENTS as FINITE_REQS, _ISFINITE_SOURCE
from asmpython._backends.arm64._verify_float_isinf import _EXPECTED_REQUIREMENTS as INF_REQS, _ISINF_SOURCE
from asmpython._backends.arm64._verify_float_isnan import _EXPECTED_REQUIREMENTS as NAN_REQS, _ISNAN_SOURCE
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _abs_bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0] & 0x7FFF_FFFF_FFFF_FFFF


class Arm64FloatClassifyTests(unittest.TestCase):
    def test_each_probe_has_its_own_exact_symbol_surface(self) -> None:
        for source, expected in (
            (_ISNAN_SOURCE, NAN_REQS),
            (_ISINF_SOURCE, INF_REQS),
            (_ISFINITE_SOURCE, FINITE_REQS),
        ):
            blob = compile_source_object(source)
            self.assertEqual(undefined_symbols(blob), expected)
            self.assertEqual(validate_runtime_requirements(blob, include_runtime=True), expected)

    def test_bit_classifiers_match_python(self) -> None:
        payload_nan = struct.unpack("<d", struct.pack("<Q", 0xFFF8_0000_0000_0042))[0]
        for value in (0.0, -0.0, 1.5, float("inf"), float("-inf"), float("nan"), payload_nan):
            magnitude = _abs_bits(value)
            self.assertEqual(magnitude > 0x7FF0_0000_0000_0000, math.isnan(value))
            self.assertEqual(magnitude == 0x7FF0_0000_0000_0000, math.isinf(value))
            self.assertEqual(magnitude < 0x7FF0_0000_0000_0000, math.isfinite(value))


if __name__ == "__main__":
    unittest.main()
