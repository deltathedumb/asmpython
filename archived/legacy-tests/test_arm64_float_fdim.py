from __future__ import annotations

import ctypes
import ctypes.util
import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_fdim import (
    _EXPECTED_REQUIREMENTS,
    _FDIM_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _model_fdim(x: float, y: float) -> float:
    if math.isnan(x):
        return x
    if math.isnan(y):
        return y
    if x > y:
        return x - y
    return 0.0


def _load_host_fdim():
    library_name = ctypes.util.find_library("m")
    try:
        library = ctypes.CDLL(library_name or None)
        function = library.fdim
    except (OSError, AttributeError):
        return None
    function.argtypes = (ctypes.c_double, ctypes.c_double)
    function.restype = ctypes.c_double
    return function


_HOST_FDIM = _load_host_fdim()


class Arm64FloatFdimTests(unittest.TestCase):
    def test_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_FDIM_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_model_properties(self) -> None:
        self.assertEqual(_bits(_model_fdim(-0.0, 0.0)), 0)
        self.assertEqual(_bits(_model_fdim(0.0, -0.0)), 0)
        self.assertTrue(math.isnan(_model_fdim(float("nan"), 1.0)))
        self.assertTrue(math.isnan(_model_fdim(1.0, float("nan"))))

    @unittest.skipIf(_HOST_FDIM is None, "host C library has no fdim")
    def test_reference_model_matches_host_c_fdim(self) -> None:
        rng = random.Random(31415926535)
        vectors = [
            (0.0, -0.0),
            (-0.0, 0.0),
            (float("inf"), float("inf")),
            (float("inf"), float("-inf")),
            (float("-inf"), 1.0),
        ]
        vectors.extend(
            (rng.uniform(-1.0e150, 1.0e150), rng.uniform(-1.0e150, 1.0e150))
            for _ in range(5_000)
        )
        for x, y in vectors:
            expected = _HOST_FDIM(x, y)
            actual = _model_fdim(x, y)
            if math.isnan(expected):
                self.assertTrue(math.isnan(actual))
            else:
                self.assertEqual(_bits(actual), _bits(expected))


if __name__ == "__main__":
    unittest.main()
