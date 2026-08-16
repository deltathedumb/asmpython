from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_del import (
    _DEL_SOURCE,
    _EXPECTED_REQUIREMENTS,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListDeletionTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_DEL_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_deletion_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_DEL_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_list_deletion(self) -> None:
        self.assertIn("_abi_list_del", RUNTIME_EXPORTS)

    def test_reference_deletion_contract(self) -> None:
        cells = [0, 1, 2, 3, 4, 5]
        del cells[0]
        del cells[2]
        del cells[3]
        del cells[-2]
        self.assertEqual(cells, [1, 4])


if __name__ == "__main__":
    unittest.main()
