from __future__ import annotations

import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_frexp_e import (
    _EXPECTED_REQUIREMENTS as EXPONENT_REQUIREMENTS,
    _FREXP_E_SOURCE,
)
from asmpython._backends.arm64._verify_float_frexp_m import (
    _EXPECTED_REQUIREMENTS as MANTISSA_REQUIREMENTS,
    _FREXP_M_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


_SIGN = 0x8000_0000_0000_0000
_ABS = 0x7FFF_FFFF_FFFF_FFFF
_FRAC = 0x000F_FFFF_FFFF_FFFF


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _from_bits(bits: int) -> float:
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def _model_mantissa(value: float) -> float:
    bits = _bits(value)
    magnitude = bits & _ABS
    exponent = magnitude >> 52
    if exponent == 0x7FF:
        return value
    if exponent == 0:
        if magnitude == 0:
            return value
        highest = magnitude.bit_length() - 1
        magnitude <<= 52 - highest
    return _from_bits((bits & _SIGN) | (1022 << 52) | (magnitude & _FRAC))


def _model_exponent(value: float) -> int:
    magnitude = _bits(value) & _ABS
    exponent = magnitude >> 52
    if exponent == 0:
        if magnitude == 0:
            return 0
        return magnitude.bit_length() - 1 - 1073
    if exponent == 0x7FF:
        return 0
    return exponent - 1022


class Arm64FloatFrexpTests(unittest.TestCase):
    def test_each_probe_has_its_own_exact_symbol_surface(self) -> None:
        for source, expected in (
            (_FREXP_M_SOURCE, MANTISSA_REQUIREMENTS),
            (_FREXP_E_SOURCE, EXPONENT_REQUIREMENTS),
        ):
            blob = compile_source_object(source)
            self.assertEqual(undefined_symbols(blob), expected)
            self.assertEqual(
                validate_runtime_requirements(blob, include_runtime=True),
                expected,
            )

    def test_models_match_python_across_binary64_bit_patterns(self) -> None:
        rng = random.Random(3141592653)
        raw_values = [
            0x0000_0000_0000_0000,
            0x8000_0000_0000_0000,
            0x0000_0000_0000_0001,
            0x8000_0000_0000_0001,
            0x000F_FFFF_FFFF_FFFF,
            0x800F_FFFF_FFFF_FFFF,
            0x7FF0_0000_0000_0000,
            0xFFF0_0000_0000_0000,
            0x7FF8_0000_0000_0042,
        ]
        raw_values.extend(rng.getrandbits(64) for _ in range(10_000))
        for bits in raw_values:
            value = _from_bits(bits)
            expected_mantissa, expected_exponent = math.frexp(value)
            actual_mantissa = _model_mantissa(value)
            actual_exponent = _model_exponent(value)
            if math.isnan(value):
                self.assertTrue(math.isnan(actual_mantissa))
            else:
                self.assertEqual(_bits(actual_mantissa), _bits(expected_mantissa))
            self.assertEqual(actual_exponent, expected_exponent)


if __name__ == "__main__":
    unittest.main()
