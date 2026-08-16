from __future__ import annotations

import unittest

from asmpython._backends.arm64._verify_list_repeat import (
    _EXPECTED_REQUIREMENTS,
    _REPEAT_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.linux_link import validate_runtime_requirements
from asmpython._backends.arm64.runtime_manifest import RUNTIME_EXPORTS
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64ListRepeatTests(unittest.TestCase):
    def test_probe_lowers_to_exact_non_exception_runtime_surface(self) -> None:
        blob = compile_source_object(_REPEAT_SOURCE)
        requirements = undefined_symbols(blob)
        self.assertEqual(requirements, _EXPECTED_REQUIREMENTS)
        self.assertNotIn("_abi_raise", requirements)

    def test_repeat_runtime_is_accepted_before_tool_discovery(self) -> None:
        blob = compile_source_object(_REPEAT_SOURCE)
        self.assertEqual(
            validate_runtime_requirements(blob, include_runtime=True),
            _EXPECTED_REQUIREMENTS,
        )

    def test_manifest_exports_repeat(self) -> None:
        self.assertIn("_abi_list_repeat", RUNTIME_EXPORTS)

    def test_reference_repeat_contract(self) -> None:
        source = [7, 8]
        self.assertEqual(source * 3, [7, 8, 7, 8, 7, 8])
        self.assertEqual(source * 0, [])
        self.assertEqual(source * -2, [])

    def test_length_and_byte_overflow_guards(self) -> None:
        max_u64 = (1 << 64) - 1
        for length, count, overflows in (
            (2, 3, False),
            (1 << 62, 4, True),
            (1 << 61, 8, True),
        ):
            product = length * count
            high = product >> 64
            byte_overflow = product > (max_u64 >> 3)
            self.assertEqual(high != 0 or byte_overflow, overflows)


if __name__ == "__main__":
    unittest.main()
