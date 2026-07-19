from __future__ import annotations

import random
import unittest

from asmpython._backends.arm64._verify_string_replace import (
    _EXPECTED_REQUIREMENTS,
    _REPLACE_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _model_replace(text: str, old: str, new: str) -> str:
    if not old:
        # Python inserts the replacement at every boundary. An empty string has
        # one boundary, not two; the generic non-empty formula would duplicate
        # `new` for that case.
        if not text:
            return new
        return new + new.join(text) + new
    output: list[str] = []
    cursor = 0
    while cursor < len(text):
        if text.startswith(old, cursor):
            output.append(new)
            cursor += len(old)
        else:
            output.append(text[cursor])
            cursor += 1
    return "".join(output)


class Arm64StringReplaceTests(unittest.TestCase):
    def test_replace_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_REPLACE_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_model_matches_python_for_utf8_cases(self) -> None:
        rng = random.Random(3140)
        alphabet = ("a", "b", "é", "λ", "🙂")
        for _ in range(500):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(8)))
            old = "".join(rng.choice(alphabet) for _ in range(rng.randrange(4)))
            new = "".join(rng.choice(alphabet) for _ in range(rng.randrange(4)))
            self.assertEqual(_model_replace(text, old, new), text.replace(old, new))


if __name__ == "__main__":
    unittest.main()
