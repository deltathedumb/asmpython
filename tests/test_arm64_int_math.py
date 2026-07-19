from __future__ import annotations

import math
import random
import unittest

from asmpython._backends.arm64._verify_int_gcd import (
    _EXPECTED_REQUIREMENTS as GCD_REQUIREMENTS,
    _GCD_SOURCE,
)
from asmpython._backends.arm64._verify_int_lcm import (
    _EXPECTED_REQUIREMENTS as LCM_REQUIREMENTS,
    _LCM_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _model_gcd(a: int, b: int) -> int:
    a = abs(a)
    b = abs(b)
    while b:
        a, b = b, a % b
    return a


def _model_lcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return abs(a) // _model_gcd(a, b) * abs(b)


class Arm64IntMathTests(unittest.TestCase):
    def test_each_probe_has_its_own_exact_symbol_surface(self) -> None:
        for source, expected in (
            (_GCD_SOURCE, GCD_REQUIREMENTS),
            (_LCM_SOURCE, LCM_REQUIREMENTS),
        ):
            blob = compile_source_object(source)
            self.assertEqual(undefined_symbols(blob), expected)
            self.assertEqual(
                validate_runtime_requirements(blob, include_runtime=True),
                expected,
            )

    def test_int64_models_match_python_in_safe_range(self) -> None:
        rng = random.Random(31415926)
        vectors = [(0, 0), (0, 7), (-54, 24), (21, -6)]
        vectors.extend(
            (rng.randrange(-1_000_000, 1_000_001), rng.randrange(-1_000_000, 1_000_001))
            for _ in range(2_000)
        )
        for a, b in vectors:
            self.assertEqual(_model_gcd(a, b), math.gcd(a, b))
            self.assertEqual(_model_lcm(a, b), math.lcm(a, b))


if __name__ == "__main__":
    unittest.main()
