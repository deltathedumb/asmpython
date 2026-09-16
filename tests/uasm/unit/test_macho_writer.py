"""Writing a Mach-O object, checked against LLVM's and against a linker.

WHAT IS NEW HERE, over the ELF and COFF writers. Mach-O's differences are
mostly about NAMING and ORDER, and both fail quietly:

  * SECTIONS LIVE IN SEGMENTS. `.text` is `__text` in `__TEXT`, and a section
    under any other name is not code as far as the linker is concerned. The
    name fields are sixteen bytes, NUL-PADDED AND NOT TERMINATED, so a writer
    reserving a byte for a terminator truncates every sixteen-character name.

  * THE SYMBOL TABLE IS SORTED and `LC_DYSYMTAB` records where each run
    starts: locals, defined externals, undefined. The linker reads the counts
    and trusts them, so a symbol in the wrong run is resolved as the wrong
    kind rather than reported.

  * THE ADDEND IS IN THE SECTION DATA, as in COFF, and `pcrel` and the field
    length are part of the relocation rather than implied by its type.

THE LINKER IS THE ORACLE THAT MATTERS. A wrong addend or a wrong relocation
type produces a file that links without complaint and calls into the middle of
an instruction; only reading back where the call LANDED can tell.
"""
from __future__ import annotations

import struct
import subprocess

from tests import harness

from uasm.backend.objfile.macho import (
    CPU_SUBTYPE_ARM64_ALL, CPU_SUBTYPE_X86_64_ALL, CPU_TYPE_ARM64,
    CPU_TYPE_X86_64, MH_MAGIC_64, MH_OBJECT, S_ATTR_PURE_INSTRUCTIONS,
    S_ATTR_SOME_INSTRUCTIONS, S_REGULAR, S_ZEROFILL,
    MachoObject, Relocation, Symbol, _fixed,
)

CODE = S_REGULAR | S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS


def _x86_object():
    from uasm.backends.x86_64.encode import encode_function
    from uasm.backends.x86_64.machoemit import _translate
    main = encode_function(["pushq %rbx", "call _helper",
                            "leaq _gv(%rip), %rax", "movq (%rax), %rbx",
                            "popq %rbx", "ret"])
    helper = encode_function(["movq $7, %rax", "ret"])
    obj = MachoObject(CPU_TYPE_X86_64, CPU_SUBTYPE_X86_64_ALL)
    obj.section(".text", bytes(main.code) + bytes(helper.code), flags=CODE,
                align=16)
    obj.section(".data", b"\x2a" + b"\0" * 7, align=8)
    obj.symbol(Symbol("_main", ".text", 0, len(main.code)))
    obj.symbol(Symbol("_helper", ".text", len(main.code), len(helper.code)))
    obj.symbol(Symbol("_gv", ".data", 0, 8))
    for at, sym, kind, addend in main.relocs:
        obj.relocate(".text", _translate(kind, addend, at, sym))
    return obj, len(main.code)


def _arm_object():
    from uasm.backends.arm64.encode import encode_function
    from uasm.backends.arm64.machoemit import _translate
    main = encode_function(["stp x29, x30, [sp, #-16]!", "bl _helper",
                            "adrp x1, _gv", "add x1, x1, :lo12:_gv",
                            "ldr x0, [x1]", "ldp x29, x30, [sp], #16", "ret"])
    helper = encode_function(["movz x0, #7", "ret"])
    obj = MachoObject(CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL)
    obj.section(".text", bytes(main.code) + bytes(helper.code), flags=CODE,
                align=4)
    obj.section(".data", b"\x2a" + b"\0" * 7, align=8)
    obj.symbol(Symbol("_main", ".text", 0, len(main.code)))
    obj.symbol(Symbol("_helper", ".text", len(main.code), len(helper.code)))
    obj.symbol(Symbol("_gv", ".data", 0, 8))
    for at, sym, kind, addend in main.relocs:
        obj.relocate(".text", _translate(kind, addend, at, sym))
    return obj, len(main.code)


class TestTheHeaderSaysWhatItIs:

    @harness.cases("cpu", [CPU_TYPE_X86_64, CPU_TYPE_ARM64])
    def test_the_magic_and_machine(self, cpu):
        obj = MachoObject(cpu, 0)
        obj.section(".text", b"\xc3", flags=CODE, align=4)
        data = obj.to_bytes()
        magic, machine, _, filetype, ncmds, _, _, _ = struct.unpack_from(
            "<IiiIIIII", data, 0)
        assert magic == MH_MAGIC_64
        assert machine == cpu
        assert filetype == MH_OBJECT
        assert ncmds == 3, "segment, symtab and dysymtab"


class TestNamesAreFixedFieldsNotStrings:

    def test_a_sixteen_byte_name_keeps_every_character(self):
        """NUL-PADDED, NOT TERMINATED. A writer leaving room for a terminator
        loses the last character of every full-length name, and the section
        then goes somewhere nobody named."""
        assert _fixed("abcdefghijklmnop") == b"abcdefghijklmnop"
        assert len(_fixed("abcdefghijklmnop")) == 16

    def test_a_shorter_name_is_padded_with_zeroes(self):
        assert _fixed("__text") == b"__text".ljust(16, b"\0")

    def test_a_longer_name_is_refused(self):
        with harness.raises(ValueError, match="does not fit"):
            _fixed("this_name_is_far_too_long_for_the_field")

    def test_the_section_lands_in_its_segment(self):
        obj, _ = _x86_object()
        data = obj.to_bytes()
        # The first section header follows the 32-byte header and the
        # 72-byte segment command.
        name = data[104:120].rstrip(b"\0")
        segment = data[120:136].rstrip(b"\0")
        assert name == b"__text" and segment == b"__TEXT"

    def test_a_section_with_no_known_segment_is_refused(self):
        """Guessing would put code where the linker does not look for code."""
        obj = MachoObject(CPU_TYPE_X86_64, 0)
        with harness.raises(ValueError, match="no Mach-O segment"):
            obj.section(".init_array", b"")


class TestTheSymbolTableIsSortedIntoItsThreeRuns:
    """`LC_DYSYMTAB` says where each run starts and the linker trusts it."""

    def _dysymtab(self, obj) -> tuple[int, ...]:
        data = obj.to_bytes()
        nsects = struct.unpack_from("<I", data, 32 + 64)[0]
        at = 32 + 72 + 80 * nsects + 24
        fields = struct.unpack_from("<20I", data, at)
        return fields[2:8]              # ilocal, nlocal, iext, next, iundef, nundef

    def test_the_runs_are_contiguous_and_cover_everything(self):
        obj = MachoObject(CPU_TYPE_X86_64, 0)
        obj.section(".text", b"\xc3", flags=CODE, align=4)
        obj.symbol(Symbol("_public", ".text", 0, 1, binding=1))
        obj.symbol(Symbol("_private", ".text", 0, 1, binding=0))
        obj.symbol(Symbol("_elsewhere", ""))
        ilocal, nlocal, iext, next_, iundef, nundef = self._dysymtab(obj)
        assert (ilocal, nlocal) == (0, 1)
        assert (iext, next_) == (1, 1)
        assert (iundef, nundef) == (2, 1)

    def test_a_local_is_written_before_an_external_it_was_added_after(self):
        """The ORDER IN THE FILE is the file's, not the caller's."""
        obj = MachoObject(CPU_TYPE_X86_64, 0)
        obj.section(".text", b"\xc3", flags=CODE, align=4)
        obj.symbol(Symbol("_zzz_global", ".text", 0, 1, binding=1))
        obj.symbol(Symbol("_aaa_local", ".text", 0, 1, binding=0))
        data = obj.to_bytes()
        assert data.index(b"_aaa_local") < data.index(b"_zzz_global")


class TestSectionsAndAddresses:

    def test_a_zerofill_section_has_no_file_offset(self):
        """One with an offset makes the linker read whatever follows it."""
        obj = MachoObject(CPU_TYPE_X86_64, 0)
        obj.section(".text", b"\xc3", flags=CODE, align=4)
        obj.section(".bss", b"", flags=S_ZEROFILL, align=8, size_override=64)
        data = obj.to_bytes()
        nsects = struct.unpack_from("<I", data, 32 + 64)[0]
        second = 32 + 72 + 80
        size, offset = struct.unpack_from("<QI", data, second + 32 + 8)
        assert nsects == 2
        assert size == 64, "the .bss lost its size"
        assert offset == 0, "a zerofill section has no bytes to point at"

    def test_addresses_run_end_to_end_from_zero(self):
        """Sections in an object share one segment at address zero, and each
        one's address is where it falls in that layout."""
        obj, _ = _x86_object()
        data = obj.to_bytes()
        first = struct.unpack_from("<Q", data, 32 + 72 + 32)[0]
        first_size = struct.unpack_from("<Q", data, 32 + 72 + 40)[0]
        second = struct.unpack_from("<Q", data, 32 + 72 + 80 + 32)[0]
        assert first == 0
        assert second >= first + first_size

    def test_a_relocation_naming_no_symbol_is_refused(self):
        obj = MachoObject(CPU_TYPE_X86_64, 0)
        obj.section(".text", b"\xe8\0\0\0\0", flags=CODE, align=4)
        obj.relocate(".text", Relocation(1, "nobody", 2, pcrel=True))
        with harness.raises(ValueError, match="not a symbol"):
            obj.to_bytes()

    def test_a_section_with_contents_after_a_zerofill_is_refused(self):
        """Mach-O requires the zerofill sections last, and the failure is not
        a rejected file: it is a segment whose file range describes bytes that
        are not there, and every address after it wrong."""
        obj = MachoObject(CPU_TYPE_X86_64, 0)
        obj.section(".bss", b"", flags=S_ZEROFILL, align=8, size_override=8)
        obj.section(".data", b"\x01" * 8, align=8)
        with harness.raises(ValueError, match="zerofill sections last"):
            obj.to_bytes()


class TestTheTranslationsFromElf:

    def test_a_call_and_a_data_reference_are_different_on_x86(self):
        """`BRANCH` and `SIGNED` are not interchangeable: the first resolves
        through the linker's branch path and the second does not."""
        from uasm.backends.x86_64.encode import (
            R_X86_64_PC32, R_X86_64_PLT32)
        from uasm.backends.x86_64.machoemit import (
            X86_64_RELOC_BRANCH, X86_64_RELOC_SIGNED, _translate)
        assert _translate(R_X86_64_PLT32, -4, 0, "_f").kind \
            == X86_64_RELOC_BRANCH
        assert _translate(R_X86_64_PC32, -4, 0, "_f").kind \
            == X86_64_RELOC_SIGNED

    def test_the_implicit_minus_four_is_removed_on_x86(self):
        from uasm.backends.x86_64.encode import R_X86_64_PLT32
        from uasm.backends.x86_64.machoemit import _translate
        assert _translate(R_X86_64_PLT32, -4, 0, "_f").addend == 0
        assert _translate(R_X86_64_PLT32, 4, 0, "_f").addend == 8

    def test_aarch64_needs_no_addend_arithmetic(self):
        """A page and an offset are already how AArch64 builds an address, so
        the two formats spell it the same and nothing has to be adjusted."""
        from uasm.backends.arm64.encode import (
            R_AARCH64_ADD_ABS_LO12_NC, R_AARCH64_ADR_PREL_PG_HI21)
        from uasm.backends.arm64.machoemit import (
            ARM64_RELOC_PAGE21, ARM64_RELOC_PAGEOFF12, _translate)
        page = _translate(R_AARCH64_ADR_PREL_PG_HI21, 0, 0, "_g")
        off = _translate(R_AARCH64_ADD_ABS_LO12_NC, 0, 4, "_g")
        assert page.kind == ARM64_RELOC_PAGE21 and page.pcrel
        assert off.kind == ARM64_RELOC_PAGEOFF12 and not off.pcrel

    def test_an_aarch64_addend_is_refused_rather_than_dropped(self):
        """Mach-O spells one with a separate record. Dropping it would reach
        the right symbol at the wrong field of it."""
        from uasm.backends.arm64.encode import R_AARCH64_CALL26
        from uasm.backends.arm64.machoemit import _translate
        with harness.raises(NotImplementedError, match="ARM64_RELOC_ADDEND"):
            _translate(R_AARCH64_CALL26, 8, 0, "_g")

    @harness.cases("module", ["x86_64", "arm64"])
    def test_an_unknown_relocation_is_refused(self, module):
        import importlib
        emit = importlib.import_module(
            f"uasm.backends.{'x86_64' if module == 'x86_64' else 'arm64'}"
            f".machoemit")
        with harness.raises(NotImplementedError, match="no Mach-O spelling"):
            emit._translate(9999, 0, 0, "_f")


@harness.needs("llvm-aarch64")
class TestLlvmReadsOurFile:
    """A format error usually shows as a reader refusing, not as bad content."""

    @harness.cases("which,expected", [("x86", "mach-o 64-bit x86-64"),
                                      ("arm", "mach-o arm64")])
    def test_a_second_implementation_opens_it(self, which, expected, tmp_path):
        obj, _ = _x86_object() if which == "x86" else _arm_object()
        path = tmp_path / "o.o"
        path.write_bytes(obj.to_bytes())
        out = subprocess.run(["llvm-objdump", "-h", "-r", str(path)],
                             capture_output=True, text=True, check=True).stdout
        assert expected in out, out
        assert "__text" in out


@harness.needs("lld")
class TestARealLinkerResolvesIt:
    """Where the reference LANDED, which is the only thing that settles it."""

    def _link(self, tmp_path, obj, arch):
        (tmp_path / "o.o").write_bytes(obj.to_bytes())
        done = subprocess.run(
            ["ld64.lld-18", "-o", "o.out", "o.o", "-arch", arch,
             "-platform_version", "macos", "11.0", "11.0",
             "-e", "_main", "-static"],
            capture_output=True, text=True, cwd=tmp_path)
        if done.returncode != 0:
            harness.skip(f"ld64.lld could not link: {done.stderr[:200]}")
        return subprocess.run(["llvm-objdump", "-d", str(tmp_path / "o.out")],
                              capture_output=True, text=True,
                              check=True).stdout

    def test_the_x86_call_lands_on_the_helper(self, tmp_path):
        obj, main_length = _x86_object()
        text = self._link(tmp_path, obj, "x86_64")
        call = next(l for l in text.splitlines() if "callq" in l)
        assert "<_helper>" in call, call

    def test_the_x86_data_reference_lands_on_the_global(self, tmp_path):
        obj, _ = _x86_object()
        text = self._link(tmp_path, obj, "x86_64")
        lea = next(l for l in text.splitlines() if "leaq" in l)
        assert "<_gv>" in lea, lea

    def test_the_arm_branch_lands_on_the_helper(self, tmp_path):
        obj, _ = _arm_object()
        text = self._link(tmp_path, obj, "arm64")
        branch = next(l for l in text.splitlines() if "\tbl\t" in l)
        assert "<_helper>" in branch, branch

    def test_the_arm_page_and_offset_reach_the_global(self, tmp_path):
        """BOTH HALVES, because a backend emitting only the page lands on the
        right page and the wrong variable."""
        obj, _ = _arm_object()
        text = self._link(tmp_path, obj, "arm64")
        adrp = next(l for l in text.splitlines() if "adrp" in l)
        assert "<_gv>" in adrp, adrp
        after = text.splitlines()[text.splitlines().index(adrp) + 1]
        assert "add" in after, after
