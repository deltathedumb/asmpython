from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_insert import (
    _EXPECTED_REQUIREMENTS,
    _INSERT_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListInsertTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_INSERT_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_insert_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_INSERT_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_insert(self) -> None:
        self.assertIn("_abi_list_insert", RUNTIME_EXPORTS)

    def test_reference_index_clamping_and_shift(self) -> None:
        cells = [1, 3]
        for index, value in ((1, 2), (-99, 0), (99, 4)):
            length = len(cells)
            if index < 0:
                index += length
                if index < 0:
                    index = 0
            elif index > length:
                index = length
            cells.append(value)
            cursor = length
            while cursor > index:
                cells[cursor] = cells[cursor - 1]
                cursor -= 1
            cells[index] = value
        self.assertEqual(cells, [0, 1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
