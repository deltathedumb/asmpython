"""Writing a PE/COFF object, checked against LLVM's and against a linker.

WHAT IS ACTUALLY NEW HERE. The ELF writer already proved that this compiler
can lay out sections, symbols and relocations; COFF differs in a handful of
places, and every one of them is silent when wrong -- the file still parses,
the linker still takes it, and the program is wrong. So the tests are aimed at
the differences rather than spread evenly:

  * THE ADDEND. COFF has no field for one, so it goes into the section data.
    ELF spells a call as "the symbol, minus four"; COFF's `REL32` measures
    from the end of its own field already, so the same call carries nothing.
    Copying -4 across moves every call four bytes earlier, into the middle of
    the previous instruction.
  * `.bss` SIZE lives in `SizeOfRawData`, not in `VirtualSize`. The names say
    otherwise and `VirtualSize` is where ELF's analogue would go, so a `.bss`
    described through it comes out as zero bytes everywhere.
  * ALIGNMENT is log2 PLUS ONE, so a writer computing plain log2 marks every
    16-byte section as 8-byte aligned and the linker honours it.

WHY BYTE-FOR-BYTE AGAINST llvm-mc ONLY FOR A SNIPPET WITH NO LOCAL BRANCHES.
An assembler RELAXES: `jmp .L` a few bytes away becomes two bytes where this
encoder always writes five. Both are correct and the lengths differ, so a
whole function cannot be compared this way -- see `test_arm64_object.py`,
where every instruction is four bytes and the comparison does work.
"""
from __future__ import annotations

import struct
import subprocess

from tests import harness

from uasm.backend.objfile import (
    IMAGE_FILE_MACHINE_AMD64, IMAGE_SCN_CNT_CODE,
    IMAGE_SCN_CNT_UNINITIALIZED_DATA, IMAGE_SCN_MEM_EXECUTE,
    IMAGE_SCN_MEM_READ, IMAGE_SCN_MEM_WRITE,
    CoffObject, CoffRelocation, CoffSymbol,
)
from uasm.backends.x86_64.coffemit import (
    IMAGE_REL_AMD64_REL32, _translate,
)
from uasm.backends.x86_64.encode import (
    R_X86_64_PC32, R_X86_64_PLT32, encode_function,
)

CODE = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ

#: A function with an external call and a data reference, and NO local branch
#: -- see the module docstring for why that matters.
SNIPPET = ["pushq %rbx", "call helper", "leaq gv(%rip), %rax",
           "movq (%rax), %rbx", "call helper", "popq %rbx", "ret"]


def _object(lines=SNIPPET) -> CoffObject:
    enc = encode_function(lines)
    obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
    obj.section(".text", bytes(enc.code), characteristics=CODE, align=16)
    obj.symbol(CoffSymbol("f", ".text", 0, len(enc.code), 1, 2))
    obj.symbol(CoffSymbol("helper", "", binding=1))
    obj.symbol(CoffSymbol("gv", "", binding=1))
    for at, sym, kind, addend in enc.relocs:
        number, stored = _translate(kind, addend)
        obj.relocate(".text", CoffRelocation(at, sym, number, stored))
    return obj


class TestTheElfAddendBecomesTheCoffOne:
    """The one piece of arithmetic in the translation, on its own.

    `REL32` HAS AN IMPLICIT -4 and ELF's `PC32` states it, so the two spell
    the same reference with addends four apart. Getting this backwards is the
    difference between a call landing on its target and landing four bytes
    before it.
    """

    @harness.cases("kind", [R_X86_64_PC32, R_X86_64_PLT32])
    def test_minus_four_becomes_nothing_stored(self, kind):
        assert _translate(kind, -4) == (IMAGE_REL_AMD64_REL32, 0)

    @harness.cases("kind", [R_X86_64_PC32, R_X86_64_PLT32])
    def test_a_further_offset_survives(self, kind):
        """`sym+8` must still mean `sym+8` after the implicit -4 is removed."""
        assert _translate(kind, 4) == (IMAGE_REL_AMD64_REL32, 8)

    def test_an_unknown_relocation_is_refused(self):
        """Guessing a number would produce a file the linker reads as a
        different relocation entirely."""
        with harness.raises(NotImplementedError, match="no COFF spelling"):
            _translate(9999, 0)


class TestTheFileSaysWhatItIs:

    def test_the_machine_and_counts_are_in_the_header(self):
        data = _object().to_bytes()
        machine, sections, _, symtab, symbols, opt, _ = struct.unpack_from(
            "<HHIIIHH", data, 0)
        assert machine == IMAGE_FILE_MACHINE_AMD64
        assert sections == 1 and symbols == 3
        assert opt == 0, "an object file has no optional header"
        assert symtab == len(data) - (18 * symbols) - _string_table_size(data)

    def test_a_bss_carries_its_size_where_coff_keeps_it(self):
        """`SizeOfRawData`, with no file offset. See the module docstring."""
        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".bss", b"", size_override=4096, align=8,
                    characteristics=IMAGE_SCN_CNT_UNINITIALIZED_DATA
                    | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE)
        data = obj.to_bytes()
        virtual, _, raw_size, raw_at = struct.unpack_from("<IIII", data, 20 + 8)
        assert raw_size == 4096, "the size is not where a linker reads it"
        assert raw_at == 0, "a .bss with a file offset makes the loader read"
        assert virtual == 0, "VirtualSize is zero in an object file"

    @harness.cases("align,expected", [(1, 1), (2, 2), (4, 3), (8, 4),
                                      (16, 5), (32, 6), (4096, 13)])
    def test_alignment_is_log2_plus_one(self, align, expected):
        """ONE-BASED, so zero can mean "unspecified". A writer computing plain
        log2 marks a 16-byte section as 8-byte aligned, and lld honours it."""
        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".text", b"\xc3", characteristics=CODE, align=align)
        data = obj.to_bytes()
        characteristics, = struct.unpack_from("<I", data, 20 + 36)
        assert (characteristics & 0x00F00000) >> 20 == expected

    def test_a_long_section_name_spills_into_the_string_table(self):
        """Eight bytes is the field. COFF spells anything longer as a slash
        and the decimal offset, in ASCII, inside those same eight bytes."""
        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".note.GNU-stack", b"", characteristics=0, align=1)
        data = obj.to_bytes()
        name = data[20:28]
        assert name.startswith(b"/"), name
        assert name.rstrip(b"\0")[1:].isdigit(), name

    def test_a_long_symbol_name_spills_too(self):
        """Four zero bytes, then the offset -- a different spelling from the
        section one, which is a slash and ASCII digits."""
        long = "apy_a_runtime_symbol_longer_than_eight"
        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".text", b"\xc3", characteristics=CODE, align=16)
        obj.symbol(CoffSymbol(long, ".text", 0, 1, 1, 2))
        data = obj.to_bytes()
        symtab, = struct.unpack_from("<I", data, 8)
        zero, offset = struct.unpack_from("<II", data, symtab)
        assert zero == 0
        assert long.encode() in data[symtab + 18:]

    def test_a_relocation_naming_no_symbol_is_refused(self):
        """COFF names a symbol by INDEX, so one with no record is not a
        missing-symbol message: it is an index into whatever is there."""
        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".text", b"\xe8\0\0\0\0", characteristics=CODE)
        # RECORDING IT IS FINE; WRITING IT IS NOT. The symbols are not all
        # known until the file is laid out, so the check belongs at write
        # time -- and only `to_bytes` goes inside the block, or a reader
        # cannot tell which of the two calls is the one that refuses.
        obj.relocate(".text", CoffRelocation(1, "nobody",
                                             IMAGE_REL_AMD64_REL32))
        with harness.raises(ValueError, match="not a symbol"):
            obj.to_bytes()


def _string_table_size(data: bytes) -> int:
    symtab, symbols = struct.unpack_from("<II", data, 8)
    return struct.unpack_from("<I", data, symtab + 18 * symbols)[0]


@harness.needs("llvm-aarch64")
class TestLlvmAgreesAboutTheSameSnippet:
    """A second implementation of the format, on input it cannot relax."""

    def _theirs(self, tmp_path):
        source = ("\t.text\n\t.globl f\nf:\n"
                  + "".join("\t" + one + "\n" for one in SNIPPET))
        (tmp_path / "a.s").write_text(source, encoding="utf-8")
        subprocess.run(
            ["llvm-mc", "-triple=x86_64-windows-msvc", "-filetype=obj",
             "-o", str(tmp_path / "theirs.obj"), str(tmp_path / "a.s")],
            check=True, capture_output=True)
        return tmp_path / "theirs.obj"

    def _relocations(self, path) -> list[tuple[str, str]]:
        out = subprocess.run(["llvm-objdump", "-r", str(path)],
                             capture_output=True, text=True, check=True).stdout
        rows = []
        for line in out.splitlines():
            if "IMAGE_REL" not in line:
                continue
            offset, kind, symbol = line.split()
            rows.append((offset.lstrip("0") or "0", kind + " " + symbol))
        return rows

    def test_the_relocations_are_the_same_records(self, tmp_path):
        ours = tmp_path / "ours.obj"
        ours.write_bytes(_object().to_bytes())
        assert self._relocations(ours) == self._relocations(self._theirs(tmp_path))

    def test_llvm_reads_our_file(self, tmp_path):
        """A format error usually shows up as a reader refusing, not as bad
        content -- so that a second implementation opens it at all is worth
        asserting on its own."""
        ours = tmp_path / "ours.obj"
        ours.write_bytes(_object().to_bytes())
        out = subprocess.run(["llvm-objdump", "-h", str(ours)],
                             capture_output=True, text=True, check=True).stdout
        assert "coff-x86-64" in out and ".text" in out


@harness.needs("lld")
class TestARealLinkerResolvesIt:
    """The proof that the addend translation is right, rather than plausible.

    A wrong addend produces a file that links without complaint and calls four
    bytes into the middle of an instruction. Only reading back where the call
    LANDED can tell the difference.
    """

    MAIN = ["pushq %rbx", "call helper", "leaq gv(%rip), %rax",
            "movq (%rax), %rbx", "popq %rbx", "ret"]
    HELPER = ["movq $7, %rax", "ret"]

    def _linked(self, tmp_path):
        main, helper = encode_function(self.MAIN), encode_function(self.HELPER)
        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".text", bytes(main.code) + bytes(helper.code),
                    characteristics=CODE, align=16)
        obj.section(".data", b"\x2a" + b"\0" * 7, align=8,
                    characteristics=0x40000040)
        obj.symbol(CoffSymbol("entry", ".text", 0, len(main.code), 1, 2))
        obj.symbol(CoffSymbol("helper", ".text", len(main.code),
                              len(helper.code), 1, 2))
        obj.symbol(CoffSymbol("gv", ".data", 0, 8, 1))
        for at, sym, kind, addend in main.relocs:
            number, stored = _translate(kind, addend)
            obj.relocate(".text", CoffRelocation(at, sym, number, stored))
        (tmp_path / "o.obj").write_bytes(obj.to_bytes())
        done = subprocess.run(
            ["lld-link", "/out:o.exe", "o.obj", "/entry:entry",
             "/subsystem:console", "/nodefaultlib", "/base:0x140000000"],
            capture_output=True, text=True, cwd=tmp_path)
        assert done.returncode == 0, done.stdout + done.stderr
        return subprocess.run(
            ["llvm-objdump", "-d", str(tmp_path / "o.exe")],
            capture_output=True, text=True, check=True).stdout

    def test_the_call_lands_on_the_helper(self, tmp_path):
        text = self._linked(tmp_path)
        call = next(l for l in text.splitlines() if "callq" in l)
        target = int(call.split("callq")[1].split()[0].strip(), 16)
        # `helper` is the first instruction after `entry`, and `entry` is
        # eighteen bytes: a push, a call, a lea, a load, a pop and a ret.
        entry = next(l for l in text.splitlines() if "pushq" in l)
        start = int(entry.split(":")[0], 16)
        assert target == start + len(encode_function(self.MAIN).code), (
            f"the call landed at {target:#x}, not on the helper")

    def test_the_data_reference_lands_in_the_data_section(self, tmp_path):
        text = self._linked(tmp_path)
        lea = next(l for l in text.splitlines() if "leaq" in l)
        assert "#" in lea, lea
        resolved = int(lea.split("#")[1].strip(), 16)
        assert resolved % 0x1000 == 0, (
            f"the reference resolved to {resolved:#x}, which is not the "
            f"start of the data section")
