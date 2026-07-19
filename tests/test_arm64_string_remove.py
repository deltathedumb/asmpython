from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_string_remove import (
    _EXPECTED_REQUIREMENTS,
    _REMOVE_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64StringRemoveTests(unittest.TestCase):
    def test_remove_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_REMOVE_SOURCE)
        self.assertEqual(undefined_symbols(blob), _EXPECTED_REQUIREMENTS)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )


if __name__ == "__main__":
    unittest.main()
