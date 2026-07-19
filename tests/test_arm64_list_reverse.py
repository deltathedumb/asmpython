from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_reverse import (
    _EXPECTED_REQUIREMENTS,
    _REVERSE_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListReverseTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_REVERSE_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_reverse_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_REVERSE_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_reverse(self) -> None:
        self.assertIn("_abi_list_reverse", RUNTIME_EXPORTS)

    def test_reference_reverse_contract_for_even_and_odd_lengths(self) -> None:
        for values, expected in (
            ([1, 2, 3, 4], [4, 3, 2, 1]),
            ([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]),
            ([], []),
            ([1], [1]),
        ):
            cells = list(values)
            left = 0
            right = len(cells) - 1
            while left < right:
                cells[left], cells[right] = cells[right], cells[left]
                left += 1
                right -= 1
            self.assertEqual(cells, expected)


if __name__ == "__main__":
    unittest.main()
