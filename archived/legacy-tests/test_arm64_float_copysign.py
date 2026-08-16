from __future__ import annotations

import math
import struct
import unittest

from asmpython._backends.arm64._verify_float_copysign import (
    _COPYSIGN_SOURCE,
    _EXPECTED_REQUIREMENTS,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _copysign_bits(magnitude: float, sign: float) -> float:
    mag_bits = struct.unpack("<Q", struct.pack("<d", magnitude))[0]
    sign_bits = struct.unpack("<Q", struct.pack("<d", sign))[0]
    result = (mag_bits & 0x7FFF_FFFF_FFFF_FFFF) | (
        sign_bits & 0x8000_0000_0000_0000
    )
    return struct.unpack("<d", struct.pack("<Q", result))[0]


class Arm64FloatCopysignTests(unittest.TestCase):
    def test_copysign_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_COPYSIGN_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_operation_replaces_only_the_sign_bit(self) -> None:
        vectors = (
            (3.5, -1.0),
            (-2.5, 1.0),
            (0.0, -0.0),
            (float("inf"), -1.0),
        )
        for magnitude, sign in vectors:
            actual = _copysign_bits(magnitude, sign)
            expected = math.copysign(magnitude, sign)
            self.assertEqual(
                struct.pack("<d", actual),
                struct.pack("<d", expected),
            )
        payload_nan = struct.unpack(
            "<d", struct.pack("<Q", 0x7FF8_0000_0000_0042)
        )[0]
        result_bits = struct.unpack(
            "<Q", struct.pack("<d", _copysign_bits(payload_nan, -0.0))
        )[0]
        self.assertEqual(result_bits, 0xFFF8_0000_0000_0042)


if __name__ == "__main__":
    unittest.main()
