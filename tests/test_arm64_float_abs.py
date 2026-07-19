from __future__ import annotations

import struct
import unittest

from asmpython._backends.arm64._verify_float_abs import (
    _EXPECTED_REQUIREMENTS,
    _FLOAT_ABS_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _fabs_bits(value: float) -> float:
    bits = struct.unpack("<Q", struct.pack("<d", value))[0]
    bits &= 0x7FFF_FFFF_FFFF_FFFF
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


class Arm64FloatAbsTests(unittest.TestCase):
    def test_float_abs_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_FLOAT_ABS_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_operation_clears_only_the_sign_bit(self) -> None:
        for value in (-0.0, -1.5, float("-inf"), 2.25):
            self.assertEqual(_fabs_bits(value), abs(value))
        negative_nan = struct.unpack(
            "<d", struct.pack("<Q", 0xFFF8_0000_0000_0042)
        )[0]
        result_bits = struct.unpack("<Q", struct.pack("<d", _fabs_bits(negative_nan)))[0]
        self.assertEqual(result_bits, 0x7FF8_0000_0000_0042)


if __name__ == "__main__":
    unittest.main()
