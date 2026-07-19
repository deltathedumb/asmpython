from __future__ import annotations

import ctypes
import ctypes.util
import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_ceil import _CEIL_SOURCE, _EXPECTED_REQUIREMENTS as CEIL_REQS
from asmpython._backends.arm64._verify_float_floor import _FLOOR_SOURCE, _EXPECTED_REQUIREMENTS as FLOOR_REQS
from asmpython._backends.arm64._verify_float_round import _ROUND_SOURCE, _EXPECTED_REQUIREMENTS as ROUND_REQS
from asmpython._backends.arm64._verify_float_trunc import _TRUNC_SOURCE, _EXPECTED_REQUIREMENTS as TRUNC_REQS
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _load_host_function(name: str):
    try:
        library = ctypes.CDLL(ctypes.util.find_library("m") or None)
        function = getattr(library, name)
    except (OSError, AttributeError):
        return None
    function.argtypes = (ctypes.c_double,)
    function.restype = ctypes.c_double
    return function


_HOST = {name: _load_host_function(name) for name in ("ceil", "floor", "trunc", "round")}


class Arm64FloatRoundingTests(unittest.TestCase):
    def test_each_probe_has_its_own_exact_symbol_surface(self) -> None:
        for source, expected in (
            (_CEIL_SOURCE, CEIL_REQS),
            (_FLOOR_SOURCE, FLOOR_REQS),
            (_TRUNC_SOURCE, TRUNC_REQS),
            (_ROUND_SOURCE, ROUND_REQS),
        ):
            blob = compile_source_object(source)
            self.assertEqual(undefined_symbols(blob), expected)
            self.assertEqual(validate_runtime_requirements(blob, include_runtime=True), expected)

    def test_host_c_rounding_bit_contract(self) -> None:
        if any(function is None for function in _HOST.values()):
            self.skipTest("host C library lacks one or more rounding functions")
        rng = random.Random(3141592653589)
        values = [-0.0, 0.0, -0.2, 0.2, -2.5, 2.5, float("inf"), float("-inf")]
        while len(values) < 10_000:
            value = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
            if not math.isnan(value):
                values.append(value)
        for value in values:
            for function in _HOST.values():
                result = function(value)
                if math.isnan(value):
                    self.assertTrue(math.isnan(result))
                else:
                    self.assertIsInstance(_bits(result), int)
        self.assertEqual(_bits(_HOST["ceil"](-0.2)), 0x8000_0000_0000_0000)
        self.assertEqual(_bits(_HOST["floor"](0.2)), 0x0000_0000_0000_0000)
        self.assertEqual(_bits(_HOST["trunc"](-0.2)), 0x8000_0000_0000_0000)
        self.assertEqual(_HOST["round"](2.5), 3.0)
        self.assertEqual(_HOST["round"](-2.5), -3.0)


if __name__ == "__main__":
    unittest.main()
