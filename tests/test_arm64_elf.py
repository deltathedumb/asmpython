from __future__ import annotations

import struct
import unittest

from asmpython._backends.arm64.codegen import (
    FuncCode,
    R_AARCH64_ADD_ABS_LO12_NC,
    R_AARCH64_ADR_PREL_PG_HI21,
    R_AARCH64_CALL26,
)
from asmpython._backends.arm64.elf import EM_AARCH64, ET_REL, build_elf
from asmpython._compiler.ir import I64, IRGlobal


class Arm64ElfTests(unittest.TestCase):
    @staticmethod
    def _section_map(blob: bytes) -> dict[str, tuple[int, ...]]:
        header = struct.unpack_from("<16sHHIQQQIHHHHHH", blob, 0)
        section_offset = header[6]
        section_entry_size = header[11]
        section_count = header[12]
        shstr_index = header[13]

        sections = [
            struct.unpack_from("<IIQQQQIIQQ", blob, section_offset + i * section_entry_size)
            for i in range(section_count)
        ]
        shstr = sections[shstr_index]
        shstr_data = blob[shstr[4] : shstr[4] + shstr[5]]

        def name_at(offset: int) -> str:
            end = shstr_data.index(b"\x00", offset)
            return shstr_data[offset:end].decode("utf-8")

        return {name_at(section[0]): section for section in sections}

    def test_header_sections_and_relocations(self) -> None:
        caller = FuncCode(
            "caller",
            bytes(12),
            [
                (0, "callee", R_AARCH64_CALL26),
                (4, "answer", R_AARCH64_ADR_PREL_PG_HI21),
                (8, "answer", R_AARCH64_ADD_ABS_LO12_NC),
            ],
        )
        callee = FuncCode("callee", bytes.fromhex("c0035fd6"))
        answer = IRGlobal("answer", I64, 42)

        blob = build_elf([caller, callee], [answer])
        ident, elf_type, machine = struct.unpack_from("<16sHH", blob, 0)

        self.assertEqual(ident[:4], b"\x7fELF")
        self.assertEqual(elf_type, ET_REL)
        self.assertEqual(machine, EM_AARCH64)

        sections = self._section_map(blob)
        self.assertEqual(
            set(sections),
            {
                "",
                ".text",
                ".data",
                ".rodata",
                ".tdata",
                ".rela.text",
                ".symtab",
                ".strtab",
                ".shstrtab",
            },
        )

        rela = sections[".rela.text"]
        self.assertEqual(rela[5], 3 * 24)
        relocation_data = blob[rela[4] : rela[4] + rela[5]]
        relocation_types = []
        symbol_indexes = []
        for offset in range(0, len(relocation_data), 24):
            _place, info, addend = struct.unpack_from("<QQq", relocation_data, offset)
            relocation_types.append(info & 0xFFFFFFFF)
            symbol_indexes.append(info >> 32)
            self.assertEqual(addend, 0)

        self.assertEqual(
            relocation_types,
            [
                R_AARCH64_CALL26,
                R_AARCH64_ADR_PREL_PG_HI21,
                R_AARCH64_ADD_ABS_LO12_NC,
            ],
        )
        self.assertTrue(all(index > 0 for index in symbol_indexes))

        symtab = sections[".symtab"]
        strtab = sections[".strtab"]
        self.assertEqual(symtab[6], 7)  # sh_link -> .strtab
        self.assertEqual(symtab[9], 24)
        self.assertEqual(rela[6], 6)  # sh_link -> .symtab
        self.assertEqual(rela[7], 1)  # sh_info -> .text
        self.assertGreater(strtab[5], 1)

    def test_unknown_relocation_is_rejected(self) -> None:
        bad = FuncCode("bad", bytes(4), [(0, "symbol", 0xFFFF)])
        with self.assertRaisesRegex(ValueError, "unsupported AArch64 text relocation"):
            build_elf([bad])


if __name__ == "__main__":
    unittest.main()
