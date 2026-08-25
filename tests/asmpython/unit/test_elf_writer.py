"""The ELF64 object writer, checked by things that are not it.

A FORMAT WRITER CANNOT BE TESTED AGAINST ITSELF. Reading back what this module
wrote with a reader from the same head would agree about every field the two
got wrong together, which is exactly the class of bug worth catching: a
`sh_info` that means something else, a `sh_link` pointing at the wrong table,
a symbol order a linker rejects. So the oracles here are outside:

  * `pyelftools`, an independent implementation, parses the structure.
  * `ld.lld`, a real linker, consumes the object and resolves a relocation
    against a second object that clang assembled.
  * the linked BL is DECODED and its target compared with the symbol's
    address, because a linker will happily accept a relocation it then
    applies somewhere harmless.

Both external tools are guarded, and the structural checks below run with
neither.
"""
from __future__ import annotations

import shutil
import struct
import subprocess

from tests import harness

from asmpython.backend.objfile import (
    EM_AARCH64, SHF_ALLOC, SHF_EXECINSTR, STB_GLOBAL, STB_LOCAL, STT_FUNC,
    ElfObject, Relocation, Symbol,
)

#: RESOLVED TO FULL PATHS, not left as names. Windows CreateProcess appends
#: `.exe` only when the name has no extension, and it reads the `.lld` in
#: `ld.lld` as one -- so the bare name is found by `which` and then fails to
#: launch, which looks like a missing tool rather than a naming rule.
LLD = shutil.which("ld.lld")
CLANG = shutil.which("clang")
HAS_LLD = bool(LLD and CLANG)
try:
    import elftools                                            # noqa: F401
    HAS_PYELFTOOLS = True
except ImportError:
    HAS_PYELFTOOLS = False

R_AARCH64_CALL26 = 283

#: `bl 0` then `ret`. The branch offset is zero because the RELOCATION is what
#: fills it in -- which is the thing under test.
CALL_THEN_RET = bytes.fromhex("00000094") + bytes.fromhex("c0035fd6")


def _object() -> ElfObject:
    o = ElfObject(EM_AARCH64)
    o.section(".text", CALL_THEN_RET,
              flags=SHF_ALLOC | SHF_EXECINSTR, align=4)
    o.symbol(Symbol("adder", ".text", 0, len(CALL_THEN_RET),
                    STB_GLOBAL, STT_FUNC))
    o.symbol(Symbol("helper", "", binding=STB_GLOBAL))
    o.relocate(".text", Relocation(0, "helper", R_AARCH64_CALL26))
    return o


class TestTheBytesAreAnElf:
    """What can be checked with nothing installed."""

    def test_the_magic_and_class_are_right(self):
        data = _object().to_bytes()
        assert data[:4] == b"\x7fELF"
        assert data[4] == 2, "EI_CLASS is not ELFCLASS64"
        assert data[5] == 1, "EI_DATA is not little-endian"

    def test_it_is_relocatable_and_names_its_machine(self):
        data = _object().to_bytes()
        e_type, e_machine = struct.unpack_from("<HH", data, 16)
        assert e_type == 1, "not ET_REL"
        assert e_machine == EM_AARCH64

    def test_a_relocation_naming_no_symbol_is_refused(self):
        """An undefined symbol must still be DECLARED.

        Silently dropping the relocation would produce an object that links
        and calls address zero.
        """
        o = ElfObject(EM_AARCH64)
        o.section(".text", CALL_THEN_RET, flags=SHF_ALLOC | SHF_EXECINSTR)
        o.relocate(".text", Relocation(0, "nowhere", R_AARCH64_CALL26))
        try:
            o.to_bytes()
        except KeyError as exc:
            assert "nowhere" in str(exc)
        else:
            raise AssertionError("a dangling relocation was written")

    def test_locals_are_emitted_before_globals(self):
        """`sh_info` is an INDEX, so the order is part of the format.

        Checked through the symbol table's own bytes rather than through a
        reader, so it holds even where pyelftools is not installed.
        """
        o = ElfObject(EM_AARCH64)
        o.section(".text", CALL_THEN_RET, flags=SHF_ALLOC | SHF_EXECINSTR)
        o.symbol(Symbol("zzz_global", ".text", binding=STB_GLOBAL))
        o.symbol(Symbol("aaa_local", ".text", binding=STB_LOCAL))
        data = o.to_bytes()
        # The local was added second and must be written first.
        assert data.index(b"aaa_local") < data.index(b"zzz_global")


@harness.skip_if(not HAS_PYELFTOOLS, reason="pyelftools not installed")
class TestAnIndependentParserAgrees:
    def _parsed(self, tmp_path):
        from elftools.elf.elffile import ELFFile
        path = tmp_path / "mine.o"
        path.write_bytes(_object().to_bytes())
        return ELFFile(path.open("rb"))

    def test_the_sections_are_all_there(self, tmp_path):
        f = self._parsed(tmp_path)
        names = {s.name for s in f.iter_sections()}
        assert {".text", ".rela.text", ".symtab", ".strtab",
                ".shstrtab"} <= names

    def test_the_symbols_say_what_they_are(self, tmp_path):
        from elftools.elf.sections import SymbolTableSection
        f = self._parsed(tmp_path)
        symtab = next(s for s in f.iter_sections()
                      if isinstance(s, SymbolTableSection))
        by_name = {s.name: s for s in symtab.iter_symbols() if s.name}
        assert by_name["adder"]["st_info"]["type"] == "STT_FUNC"
        assert by_name["adder"]["st_size"] == len(CALL_THEN_RET)
        assert by_name["helper"]["st_shndx"] == "SHN_UNDEF"

    def test_the_relocation_points_at_the_undefined_symbol(self, tmp_path):
        from elftools.elf.relocation import RelocationSection
        from elftools.elf.sections import SymbolTableSection
        f = self._parsed(tmp_path)
        rela = next(s for s in f.iter_sections()
                    if isinstance(s, RelocationSection))
        symtab = f.get_section(rela["sh_link"])
        assert isinstance(symtab, SymbolTableSection), "sh_link is wrong"
        (r,) = list(rela.iter_relocations())
        assert r["r_info_type"] == R_AARCH64_CALL26
        assert symtab.get_symbol(r["r_info_sym"]).name == "helper"


@harness.skip_if(not HAS_LLD, reason="no ld.lld and clang to link with")
class TestARealLinkerTakesIt:
    """The oracle that matters: something that was not written here.

    A linker validates far more of the format than any assertion above --
    section indices, the symbol order, the relocation encoding -- and it fails
    loudly rather than reading a wrong field as a plausible value.
    """

    COMPANION = """\
\t.text
\t.globl helper
\t.type helper, %function
helper:
\tmov x0, #41
\tret
\t.size helper, .-helper
\t.globl _start
\t.type _start, %function
_start:
\tbl adder
\tmov x8, #93
\tsvc #0
\t.size _start, .-_start
"""

    def _link(self, tmp_path):
        (tmp_path / "mine.o").write_bytes(_object().to_bytes())
        (tmp_path / "other.s").write_text(self.COMPANION, encoding="utf-8")
        done = subprocess.run(
            [CLANG, "-target", "aarch64-linux-gnu", "-c",
             str(tmp_path / "other.s"), "-o", str(tmp_path / "other.o")],
            capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        done = subprocess.run(
            [LLD, "-o", str(tmp_path / "linked.elf"),
             str(tmp_path / "mine.o"), str(tmp_path / "other.o")],
            capture_output=True, text=True)
        assert done.returncode == 0, f"lld rejected it:\n{done.stderr}"
        return tmp_path / "linked.elf"

    def test_it_links(self, tmp_path):
        assert self._link(tmp_path).stat().st_size > 0

    @harness.skip_if(not HAS_PYELFTOOLS, reason="pyelftools not installed")
    def test_the_relocation_resolves_to_the_right_address(self, tmp_path):
        """A linker will apply a relocation it accepted to the wrong place.

        So the branch is DECODED and its target compared with where `helper`
        actually landed. Nothing short of that distinguishes a correct
        relocation from one that merely parsed.
        """
        from elftools.elf.elffile import ELFFile
        from elftools.elf.sections import SymbolTableSection
        f = ELFFile(self._link(tmp_path).open("rb"))
        syms = {s.name: s["st_value"]
                for sec in f.iter_sections()
                if isinstance(sec, SymbolTableSection)
                for s in sec.iter_symbols() if s.name}
        text = next(s for s in f.iter_sections() if s.name == ".text")
        base, data = text["sh_addr"], text.data()

        at = syms["adder"] - base
        word = int.from_bytes(data[at:at + 4], "little")
        assert word >> 26 == 0b100101, f"not a BL after linking: {word:#010x}"
        imm = word & 0x03FFFFFF
        if imm & 0x02000000:                  # sign-extend the 26-bit field
            imm -= 0x04000000
        assert syms["adder"] + imm * 4 == syms["helper"], (
            f"BL reaches {syms['adder'] + imm * 4:#x}, "
            f"helper is at {syms['helper']:#x}")
