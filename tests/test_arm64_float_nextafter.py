from __future__ import annotations

import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_nextafter import (
    _EXPECTED_REQUIREMENTS,
    _NEXTAFTER_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


_SIGN = 0x8000_0000_0000_0000
_ABS = 0x7FFF_FFFF_FFFF_FFFF
_INF = 0x7FF0_0000_0000_0000


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _from_bits(bits: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def _model_nextafter(x: float, y: float) -> float:
    x_bits = _bits(x)
    y_bits = _bits(y)
    x_magnitude = x_bits & _ABS
    y_magnitude = y_bits & _ABS
    if x_magnitude > _INF:
        return x
    if y_magnitude > _INF:
        return y
    if x == y:
        return y
    if x_magnitude == 0:
        return _from_bits((y_bits & _SIGN) | 1)
    if x_bits & _SIGN:
        x_bits += 1 if x > y else -1
    else:
        x_bits += 1 if x < y else -1
    return _from_bits(x_bits)


class Arm64FloatNextafterTests(unittest.TestCase):
    def test_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_NEXTAFTER_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_model_matches_python_across_binary64_pairs(self) -> None:
        rng = random.Random(314159265358)
        pairs = [
            (0.0, -0.0),
            (-0.0, 0.0),
            (0.0, 1.0),
            (0.0, -1.0),
            (float("inf"), 0.0),
            (float("-inf"), 0.0),
        ]
        pairs.extend(
            (_from_bits(rng.getrandbits(64)), _from_bits(rng.getrandbits(64)))
            for _ in range(20_000)
        )
        for x, y in pairs:
            actual = _model_nextafter(x, y)
            expected = math.nextafter(x, y)
            if math.isnan(x) or math.isnan(y):
                self.assertTrue(math.isnan(actual))
            else:
                self.assertEqual(_bits(actual), _bits(expected))


if __name__ == "__main__":
    unittest.main()
