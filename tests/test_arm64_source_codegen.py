from __future__ import annotations

import struct
import unittest

from asmpython._backends.arm64._verify_scalars import (
    _EXPECTED_REQUIREMENTS as _SCALAR_REQUIREMENTS,
    _SCALAR_SOURCE,
)
from asmpython._backends.arm64._verify_source import _compile_source
from asmpython._backends.arm64._verify_string_search import (
    _EXPECTED_REQUIREMENTS as _SEARCH_REQUIREMENTS,
    _SEARCH_SOURCE,
)
from asmpython._backends.arm64.elf_inspect import undefined_symbols
from asmpython._backends.arm64.source_build import compile_source_object


class Arm64SourceCodegenTests(unittest.TestCase):
    def test_integer_main_compiles_to_aarch64_object(self) -> None:
        blob = _compile_source()

        ident, elf_type, machine = struct.unpack_from("<16sHH", blob, 0)
        self.assertEqual(ident[:4], b"\x7fELF")
        self.assertEqual(elf_type, 1)  # ET_REL
        self.assertEqual(machine, 183)  # EM_AARCH64
        self.assertIn(b"main\x00", blob)

    def test_runtime_free_probe_has_no_undefined_symbol_relocations(self) -> None:
        blob = _compile_source()
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", blob, 0)
        section_offset = header[6]
        section_entry_size = header[11]
        section_count = header[12]
        shstr_index = header[13]

        sections = [
            struct.unpack_from(
                "<IIQQQQIIQQ",
                blob,
                section_offset + index * section_entry_size,
            )
            for index in range(section_count)
        ]
        shstr = sections[shstr_index]
        names = blob[shstr[4] : shstr[4] + shstr[5]]

        def section_name(section: tuple[int, ...]) -> str:
            start = section[0]
            end = names.index(b"\x00", start)
            return names[start:end].decode("utf-8")

        by_name = {section_name(section): section for section in sections}
        rela_text = by_name[".rela.text"]
        self.assertEqual(rela_text[5], 0)

    def test_scalar_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_SCALAR_SOURCE)
        self.assertEqual(undefined_symbols(blob), _SCALAR_REQUIREMENTS)

    def test_string_search_probe_lowers_to_exact_runtime_surface(self) -> None:
        blob = compile_source_object(_SEARCH_SOURCE)
        self.assertEqual(undefined_symbols(blob), _SEARCH_REQUIREMENTS)


if __name__ == "__main__":
    unittest.main()
