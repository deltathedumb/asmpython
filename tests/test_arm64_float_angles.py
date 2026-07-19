from __future__ import annotations

import math
import random
import struct
import unittest

from asmpython._backends.arm64._verify_float_degrees import _DEGREES_SOURCE, _EXPECTED_REQUIREMENTS as DEG_REQS
from asmpython._backends.arm64._verify_float_radians import _RADIANS_SOURCE, _EXPECTED_REQUIREMENTS as RAD_REQS
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _bits(value: float) -> bytes:
    return struct.pack("<d", value)


class Arm64FloatAngleTests(unittest.TestCase):
    def test_each_probe_has_its_own_exact_symbol_surface(self) -> None:
        for source, expected in ((_DEGREES_SOURCE, DEG_REQS), (_RADIANS_SOURCE, RAD_REQS)):
            blob = compile_source_object(source)
            self.assertEqual(undefined_symbols(blob), expected)
            self.assertEqual(validate_runtime_requirements(blob, include_runtime=True), expected)

    def test_factors_match_python_operation_order(self) -> None:
        rng = random.Random(314159)
        for _ in range(1000):
            value = rng.uniform(-1.0e200, 1.0e200)
            self.assertEqual(_bits(value * 57.29577951308232), _bits(math.degrees(value)))
            self.assertEqual(_bits(value * 0.017453292519943295), _bits(math.radians(value)))


if __name__ == "__main__":
    unittest.main()
