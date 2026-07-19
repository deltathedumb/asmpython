from __future__ import annotations

import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_modf_frac import (
    _EXPECTED_REQUIREMENTS as FRAC_REQUIREMENTS,
    _MODF_FRAC_SOURCE,
)
from asmpython._backends.arm64._verify_float_modf_int import (
    _EXPECTED_REQUIREMENTS as INT_REQUIREMENTS,
    _MODF_INT_SOURCE,
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


def _model_int(value: float) -> float:
    bits = _bits(value)
    magnitude = bits & _ABS
    exponent_bits = magnitude >> 52
    if exponent_bits == 0x7FF:
        return value
    exponent = exponent_bits - 1023
    if exponent < 0:
        return _from_bits(bits & _SIGN)
    if exponent >= 52:
        return value
    fraction_mask = (1 << (52 - exponent)) - 1
    return _from_bits(bits & ~fraction_mask)


def _model_frac(value: float) -> float:
    bits = _bits(value)
    magnitude = bits & _ABS
    if magnitude > _INF:
        return value
    if magnitude == _INF:
        return _from_bits(bits & _SIGN)
    result = value - _model_int(value)
    if result == 0.0:
        return _from_bits(bits & _SIGN)
    return result


class Arm64FloatModfTests(unittest.TestCase):
    def test_each_probe_has_its_own_exact_symbol_surface(self) -> None:
        for source, expected in (
            (_MODF_FRAC_SOURCE, FRAC_REQUIREMENTS),
            (_MODF_INT_SOURCE, INT_REQUIREMENTS),
        ):
            blob = compile_source_object(source)
            self.assertEqual(undefined_symbols(blob), expected)
            self.assertEqual(
                validate_runtime_requirements(blob, include_runtime=True),
                expected,
            )

    def test_models_match_python_modf_for_finite_binary64_values(self) -> None:
        rng = random.Random(314159265)
        raw_values = [
            0x0000_0000_0000_0000,
            0x8000_0000_0000_0000,
            0x0000_0000_0000_0001,
            0x8000_0000_0000_0001,
            0x3FFE_0000_0000_0000,
            0xBFFE_0000_0000_0000,
        ]
        while len(raw_values) < 4_000:
            bits = rng.getrandbits(64)
            if (bits & _ABS) < _INF:
                raw_values.append(bits)
        for bits in raw_values:
            value = _from_bits(bits)
            expected_frac, expected_int = math.modf(value)
            self.assertEqual(_bits(_model_int(value)), _bits(expected_int))
            self.assertEqual(_bits(_model_frac(value)), _bits(expected_frac))

    def test_nonfinite_and_nan_payload_contract(self) -> None:
        for bits in (0x7FF0_0000_0000_0000, 0xFFF0_0000_0000_0000):
            value = _from_bits(bits)
            expected_frac, expected_int = math.modf(value)
            self.assertEqual(_bits(_model_int(value)), _bits(expected_int))
            self.assertEqual(_bits(_model_frac(value)), _bits(expected_frac))
        payload_nan_bits = 0xFFF8_0000_0000_0042
        payload_nan = _from_bits(payload_nan_bits)
        self.assertEqual(_bits(_model_int(payload_nan)), payload_nan_bits)
        self.assertEqual(_bits(_model_frac(payload_nan)), payload_nan_bits)
        self.assertTrue(math.isnan(_model_int(payload_nan)))
        self.assertTrue(math.isnan(_model_frac(payload_nan)))


if __name__ == "__main__":
    unittest.main()
