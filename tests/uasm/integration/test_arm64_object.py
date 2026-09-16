"""The AArch64 object, proved by linking it beside one llvm-mc built.

WHAT THIS ADDS TO `test_arm64_encode`. That file checks one instruction at a
time, which is the encoder's claim. This checks the OBJECT's: that the words
land in the right order, that a branch inside a function resolves to the right
distance, and that every relocation names the right symbol with the right kind
-- three things no per-line test can see, because each is a property of the
whole file rather than of any line in it.

HOW IT IS CHECKED WITHOUT AN AArch64 MACHINE. Assemble the same module's
assembly with llvm-mc, link both objects at the same address with `ld.lld`,
and compare `.text`. Linking is what makes the comparison possible at all: our
object leaves a relocation where llvm-mc resolves a call to a symbol in the
same section itself, so the two files differ before linking and must not
after. A wrong relocation kind, a wrong addend or a wrong symbol all show up
here as different bytes.

WHY THE ASSEMBLY IS REBUILT RATHER THAN TAKEN FROM THE BACKEND. Because the
backend no longer emits any: an ELF target goes straight to the object. The
lines come from `_function` and `_global`, which is what the assembly path
used, so the two sides describe the same program.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from tests import harness

from uasm.backend import get, load_builtin
from uasm.ir.module import Linkage
from uasm.target import get as get_target

from .test_endtoend import ALL_PROGRAMS, compile_module

TRIPLE = "aarch64-linux-gnu"

#: A SPREAD RATHER THAN ALL OF THEM. Each program here reaches something the
#: others do not -- a call, a loop, a float, a class with its globals -- and
#: linking a quarter-megabyte object thirty-one times is minutes of test time
#: to re-prove what the per-line differential already covers.
PROGRAMS = ["arithmetic", "calls", "loops", "float_arithmetic",
            "class_basics", "closure_cell_is_shared", "user_exception_classes"]


def _assembly(backend, module, abi, dialect) -> str:
    """The same program as text, laid out the way the object lays it out.

    THE ZEROED GLOBALS GO IN `.bss` HERE, which the assembly path did not do:
    it wrote `.zero` inside `.data`, so the two would disagree about every
    address after the first zeroed global. Both layouts are correct and only
    one can be compared, so the text is written to match the object.
    """
    lines = [".text"]
    for fn in module.defined_functions():
        lines.extend(backend._function(fn, abi, dialect))
    initialised = [g for g in module.globals if g.data is not None]
    zeroed = [g for g in module.globals if g.data is None]
    if initialised:
        lines.append("\t.data")
        for g in initialised:
            lines.extend(backend._global(g, dialect))
    if zeroed:
        lines.append('\t.section .bss,"aw",@nobits')
        for g in zeroed:
            name = dialect.symbol_prefix + g.name
            if g.linkage is Linkage.EXPORT:
                lines.append(f"\t.globl {name}")
            lines.append("\t.align 3")
            lines.append(f"{name}:")
            lines.append(f"\t.zero {max(1, g.size)}")
    return "\n".join(lines) + "\n"


def _text_after_linking(obj: Path, tmp: Path, tag: str) -> bytes:
    """`.text` once the linker has resolved everything it can.

    `--no-relax` MATTERS. lld rewrites an `adrp`/`add` pair into `nop`/`adr`
    when the target is near enough, and whether it is near enough depends on
    the layout -- so with relaxation on, two correct objects can differ purely
    because one placed a section a few bytes elsewhere.
    """
    linked, raw = tmp / f"{tag}.elf", tmp / f"{tag}.bin"
    done = subprocess.run(
        ["ld.lld", "-o", str(linked), str(obj),
         "--unresolved-symbols=ignore-all", "-e", "0",
         "--image-base=0x100000", "--no-rosegment", "--no-relax"],
        capture_output=True, text=True)
    assert done.returncode == 0, f"linking {tag} failed:\n{done.stderr}"
    subprocess.run(
        ["llvm-objcopy", "-O", "binary", "--only-section=.text",
         str(linked), str(raw)], check=True, capture_output=True)
    return raw.read_bytes()


@harness.needs("llvm-aarch64", "lld")
@harness.cases("name", PROGRAMS)
class TestTheLinkedProgramMatchesLlvm:

    def test_the_text_sections_are_identical(self, name, tmp_path):
        from uasm.backends.arm64.emit import abi_for, dialect_for
        load_builtin()
        backend = get("arm64")
        target = get_target("aarch64-linux")
        abi, dialect = abi_for(target), dialect_for(target)
        source = textwrap.dedent(ALL_PROGRAMS[name]).strip() + "\n"
        module = compile_module(source, tmp_path, True)

        (tmp_path / "ours.o").write_bytes(
            backend.emit(module, target)["out.o"])
        (tmp_path / "theirs.s").write_text(
            _assembly(backend, module, abi, dialect), encoding="utf-8")
        subprocess.run(
            ["llvm-mc", f"-triple={TRIPLE}", "-filetype=obj",
             "-o", str(tmp_path / "theirs.o"), str(tmp_path / "theirs.s")],
            check=True, capture_output=True)

        ours = _text_after_linking(tmp_path / "ours.o", tmp_path, "ours")
        theirs = _text_after_linking(tmp_path / "theirs.o", tmp_path, "theirs")
        assert len(ours) == len(theirs), (
            f"{len(ours)} bytes against {len(theirs)}")
        for at in range(0, len(ours), 4):
            assert ours[at:at + 4] == theirs[at:at + 4], (
                f"first difference at {at:#x}: "
                f"ours {ours[at:at + 4].hex()} llvm {theirs[at:at + 4].hex()}")


@harness.needs("readelf")
class TestTheObjectIsWellFormed:
    """What the file says about itself, read by something that is not us."""

    def _object(self, tmp_path) -> Path:
        load_builtin()
        source = textwrap.dedent(ALL_PROGRAMS["class_basics"]).strip() + "\n"
        module = compile_module(source, tmp_path, True)
        path = tmp_path / "out.o"
        path.write_bytes(
            get("arm64").emit(module, get_target("aarch64-linux"))["out.o"])
        return path

    def test_it_names_aarch64(self, tmp_path):
        out = subprocess.run(["readelf", "-h", str(self._object(tmp_path))],
                             capture_output=True, text=True, check=True).stdout
        assert "AArch64" in out, out
        assert "REL (Relocatable file)" in out

    def test_the_relocations_are_the_aarch64_ones(self, tmp_path):
        """A relocation number from the wrong architecture still WRITES.

        The field is just an integer, so an object carrying x86-64's numbers
        is well formed and the linker rejects it -- or worse, finds a number
        that happens to mean something else. Reading the names back is the
        check that these are AArch64's.
        """
        out = subprocess.run(["readelf", "-rW", str(self._object(tmp_path))],
                             capture_output=True, text=True, check=True).stdout
        for wanted in ("R_AARCH64_CALL26", "R_AARCH64_ADR_PREL_PG_HI21",
                       "R_AARCH64_ADD_ABS_LO12_NC"):
            assert wanted in out, f"no {wanted} in the object"
        assert "R_X86_64" not in out
