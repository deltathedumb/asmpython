from __future__ import annotations

import unittest

from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64TypedUnpackTests(unittest.TestCase):
    def test_direct_string_and_tuple_literals_need_no_runtime_helpers(self) -> None:
        blob = compile_source_object(
            'first, second = "éx"\n'
            'label, count, ratio = ("ok", 5, 1.5)\n'
        )
        self.assertEqual(undefined_symbols(blob), frozenset())

    def test_literal_unpack_does_not_materialize_tuple_or_index_string(self) -> None:
        blob = compile_source_object(
            'a, b, c = "xyz"\n'
            'left, right = (1.25, 2.5)\n'
        )
        required = undefined_symbols(blob)
        self.assertNotIn("_abi_str_char_at", required)
        self.assertNotIn("_abi_new_list", required)
        self.assertNotIn("_abi_list_new", required)


if __name__ == "__main__":
    unittest.main()
