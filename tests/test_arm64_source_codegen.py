from __future__ import annotations

import struct
import unittest

from asmpython._backends.arm64._verify_source import _compile_source


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


if __name__ == "__main__":
    unittest.main()
