from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
import math
import struct
import unittest

from asmpython._backends.arm64._verify_float_nearbyint import _EXPECTED_REQUIREMENTS, _NEARBYINT_SOURCE
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _reference_nearbyint(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return value
    result = float(
        Decimal.from_float(value).to_integral_value(rounding=ROUND_HALF_EVEN)
    )
    if result == 0.0:
        return math.copysign(0.0, value)
    return result


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


class Arm64FloatNearbyintTests(unittest.TestCase):
    def test_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_NEARBYINT_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_default_rounding_expected_bit_vectors(self) -> None:
        vectors = (
            (-0.0, 0x8000_0000_0000_0000),
            (-0.5, 0x8000_0000_0000_0000),
            (0.5, 0x0000_0000_0000_0000),
            (1.5, 0x4000_0000_0000_0000),
            (2.5, 0x4000_0000_0000_0000),
            (3.5, 0x4010_0000_0000_0000),
            (-1.5, 0xC000_0000_0000_0000),
            (-2.5, 0xC000_0000_0000_0000),
            (float("inf"), 0x7FF0_0000_0000_0000),
            (float("-inf"), 0xFFF0_0000_0000_0000),
        )
        for value, expected_bits in vectors:
            self.assertEqual(_bits(_reference_nearbyint(value)), expected_bits)


if __name__ == "__main__":
    unittest.main()
