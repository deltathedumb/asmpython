from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_extend import (
    _EXPECTED_REQUIREMENTS,
    _EXTEND_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListExtendTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_EXTEND_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_extend_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_EXTEND_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_extend(self) -> None:
        self.assertIn("_abi_list_extend", RUNTIME_EXPORTS)

    def test_reference_self_extend_snapshots_original_cells(self) -> None:
        cells = [1, 2]
        source = list(cells)
        source_length = len(source)
        for index in range(source_length):
            cells.append(source[index])
        self.assertEqual(cells, [1, 2, 1, 2])


if __name__ == "__main__":
    unittest.main()
