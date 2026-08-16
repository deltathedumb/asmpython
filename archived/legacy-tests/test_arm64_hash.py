from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_hash import (
    _EXPECTED_REQUIREMENTS,
    _EXPECTED_STDOUT,
    _HASH_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


def _fnv1a64(text: str) -> int:
    value = 0xCBF29CE484222325
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


class Arm64HashTests(unittest.TestCase):
    def test_reference_vectors_match_probe_output(self) -> None:
        expected = "".join(
            f"{_fnv1a64(text)}\n" for text in ("hash", "asmpython", "é")
        )
        self.assertEqual(expected, _EXPECTED_STDOUT)

    def test_hash_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_HASH_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )


if __name__ == "__main__":
    unittest.main()
