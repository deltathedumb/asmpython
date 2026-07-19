from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_pop import (
    _EXPECTED_REQUIREMENTS,
    _POP_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListPopTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_POP_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_pop_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_POP_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_pop(self) -> None:
        self.assertIn("_abi_list_pop", RUNTIME_EXPORTS)

    def test_reference_pop_contract(self) -> None:
        cells = [10, 20, 30]
        length = 3
        length -= 1
        value = cells[length]
        self.assertEqual((value, length, cells[:length]), (30, 2, [10, 20]))


if __name__ == "__main__":
    unittest.main()
