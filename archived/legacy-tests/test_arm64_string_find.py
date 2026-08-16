from __future__ import annotations

import random
import unittest

from asmpython._backends.arm64._verify_string_find import (
    _EXPECTED_REQUIREMENTS,
    _FIND_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _model_find(text: str, needle: str, start: int | None = None) -> int:
    positions = [0]
    encoded = text.encode("utf-8")
    cursor = 0
    while cursor < len(encoded):
        cursor += 1
        while cursor < len(encoded) and encoded[cursor] & 0xC0 == 0x80:
            cursor += 1
        positions.append(cursor)
    normalized = 0 if start is None else start
    if normalized < 0:
        normalized = max(0, normalized + len(text))
    if normalized > len(text):
        return -1
    raw = needle.encode("utf-8")
    if not raw:
        return normalized
    for index in range(normalized, len(text)):
        offset = positions[index]
        if encoded[offset : offset + len(raw)] == raw:
            return index
    return -1


def _model_rfind(text: str, needle: str) -> int:
    if not needle:
        return len(text)
    last = -1
    start = 0
    while start <= len(text):
        found = _model_find(text, needle, start)
        if found < 0:
            return last
        last = found
        start = found + 1
    return last


class Arm64StringFindTests(unittest.TestCase):
    def test_find_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_FIND_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_reference_model_matches_python_for_utf8_cases(self) -> None:
        rng = random.Random(314)
        alphabet = ("a", "b", "é", "λ", "🙂")
        for _ in range(500):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(8)))
            needle = "".join(rng.choice(alphabet) for _ in range(rng.randrange(4)))
            start = rng.randrange(-12, 13)
            self.assertEqual(_model_find(text, needle), text.find(needle))
            self.assertEqual(_model_rfind(text, needle), text.rfind(needle))
            self.assertEqual(_model_find(text, needle, start), text.find(needle, start))


if __name__ == "__main__":
    unittest.main()
