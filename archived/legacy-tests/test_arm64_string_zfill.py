from __future__ import annotations

import random
import unittest

from asmpython._backends.arm64._verify_string_zfill import (
    _EXPECTED_REQUIREMENTS,
    _ZFILL_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _model_zfill(text: str, width: int) -> str:
    if width <= len(text):
        return text
    padding = "0" * (width - len(text))
    if text.startswith(("+", "-")):
        return text[0] + padding + text[1:]
    return padding + text


class Arm64StringZfillTests(unittest.TestCase):
    def test_zfill_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_ZFILL_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_model_matches_python_for_utf8_widths(self) -> None:
        rng = random.Random(314000)
        alphabet = ("a", "b", "é", "λ", "🙂", "+", "-")
        for _ in range(1000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(8)))
            width = rng.randrange(-5, 16)
            self.assertEqual(_model_zfill(text, width), text.zfill(width))


if __name__ == "__main__":
    unittest.main()
