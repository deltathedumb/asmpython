from __future__ import annotations

import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_ulp import (
    _EXPECTED_REQUIREMENTS,
    _ULP_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object
from asmpython.stdlib import STDLIB_BINDINGS


_ABS = 0x7FFF_FFFF_FFFF_FFFF
_INF = 0x7FF0_0000_0000_0000


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _from_bits(bits: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def _model_ulp(value: float) -> float:
    bits = _bits(value)
    magnitude = bits & _ABS
    if magnitude > _INF:
        return value
    if magnitude == _INF:
        return _from_bits(_INF)
    exponent = magnitude >> 52
    if exponent == 0:
        result_bits = 1
    elif exponent <= 52:
        result_bits = 1 << (exponent - 1)
    else:
        result_bits = (exponent - 52) << 52
    return _from_bits(result_bits)


class Arm64FloatUlpTests(unittest.TestCase):
    def test_probe_lowers_to_exact_runtime_surface(self) -> None:
        self.assertNotIn("ulp", STDLIB_BINDINGS["math"])
        blob = compile_source_object(_ULP_SOURCE)
        self.assertNotIn("ulp", STDLIB_BINDINGS["math"])
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_model_matches_python_across_binary64_values(self) -> None:
        rng = random.Random(314159265359)
        values = [
            0.0,
            -0.0,
            float("inf"),
            float("-inf"),
            float("nan"),
            _from_bits(1),
            _from_bits(0x0010_0000_0000_0000),
            _from_bits(0x7FEF_FFFF_FFFF_FFFF),
        ]
        values.extend(_from_bits(rng.getrandbits(64)) for _ in range(20_000))
        for value in values:
            actual = _model_ulp(value)
            expected = math.ulp(value)
            if math.isnan(expected):
                self.assertTrue(math.isnan(actual))
            else:
                self.assertEqual(_bits(actual), _bits(expected))


if __name__ == "__main__":
    unittest.main()
