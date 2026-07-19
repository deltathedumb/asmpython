from __future__ import annotations

import random
import unittest

from asmpython._backends.arm64._verify_string_slice import (
    _EXPECTED_REQUIREMENTS,
    _SLICE_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _model_slice(text: str, start: int, stop: int) -> str:
    length = len(text)
    if start < 0:
        start += length
    start = min(length, max(0, start))
    if stop < 0:
        stop += length
    stop = min(length, max(0, stop))
    return "" if stop <= start else text[start:stop]


class Arm64StringSliceTests(unittest.TestCase):
    def test_slice_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_SLICE_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_model_matches_python_for_utf8_bounds(self) -> None:
        rng = random.Random(31400)
        alphabet = ("a", "b", "é", "λ", "🙂")
        for _ in range(1000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(10)))
            start = rng.randrange(-15, 16)
            stop = rng.randrange(-15, 16)
            self.assertEqual(_model_slice(text, start, stop), text[start:stop])


if __name__ == "__main__":
    unittest.main()
