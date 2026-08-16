from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_slice import (
    _EXPECTED_REQUIREMENTS,
    _SLICE_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListSliceTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_SLICE_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_slice_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_SLICE_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_plain_list_slice(self) -> None:
        self.assertIn("_abi_list_slice", RUNTIME_EXPORTS)
        self.assertNotIn("_abi_list_slice_step", RUNTIME_EXPORTS)

    def test_reference_slice_contract(self) -> None:
        cells = [0, 1, 2, 3, 4]
        self.assertEqual(cells[1:4], [1, 2, 3])
        self.assertEqual(cells[:3], [0, 1, 2])
        self.assertEqual(cells[2:], [2, 3, 4])
        self.assertEqual(cells[-4:-1], [1, 2, 3])
        self.assertEqual(cells[4:2], [])
        self.assertEqual(cells[-99:99], cells)


if __name__ == "__main__":
    unittest.main()
