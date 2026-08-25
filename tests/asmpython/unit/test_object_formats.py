"""Each object format gets the directives it actually accepts.

WHY THIS FILE EXISTS. `x86_64-macos` and `aarch64-macos` had been registered
targets since the beginning, and both emitted ELF. The x86-64 backend chose
its dialect with `coff if ... else elf` and the arm64 backend had no notion of
a dialect at all, so `--target x86_64-macos` produced output BYTE-IDENTICAL to
the Linux one: `.type`, `.size` and `.note.GNU-stack`, none of which a Mach-O
assembler will take, and no leading underscore, so nothing would have resolved
even if it had assembled.

Nothing caught it because nothing compared the formats. Every test asked
whether ONE target produced good assembly, and the answer was yes for the two
that were exercised. So the assertions here are DIFFERENTIAL -- what one
format has and another must not -- because that is the shape of the bug.

The text checks need no toolchain and are the regression guard. The assembly
check needs clang, which can target all three formats from any host, and is
the one that would notice a directive this file has not thought to name.
"""
from __future__ import annotations

import shutil
import subprocess

from tests import harness

from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source

HAS_CLANG = bool(shutil.which("clang"))

#: A program with a call and a definition, so both a symbol's DEFINITION and
#: its USE are in the output -- the prefix has to reach both, and applying it
#: to only one is the failure mode that links to nothing.
SOURCE = """\
def add(a: int, b: int) -> int:
    return a + b


def main() -> int:
    return add(3, 4)
"""

#: backend, target, and the triple clang assembles that target's output with.
MATRIX = [
    ("x86-64", "x86_64-linux", "x86_64-linux-gnu"),
    ("x86-64", "x86_64-windows", "x86_64-windows-gnu"),
    ("x86-64", "x86_64-macos", "x86_64-apple-darwin"),
    ("arm64", "aarch64-linux", "aarch64-linux-gnu"),
    ("arm64", "aarch64-macos", "arm64-apple-darwin"),
    ("arm64", "aarch64-none", "aarch64-linux-gnu"),
]


def _asm(tmp_path, backend: str, target: str) -> str:
    """The assembly this backend writes for this target."""
    path = tmp_path / "prog.py"
    path.write_text(SOURCE, encoding="utf-8")
    result = compile_source(Options(
        source=path, backend=backend,
        target=target_registry.get(target)), DiagnosticSink())
    assert result.ok, f"{backend}/{target} did not compile"
    (name, body), = result.artifacts.items()
    return body.decode("utf-8")


class TestTheDialectsDiffer:
    @harness.cases("backend,target", [(b, t) for b, t, _ in MATRIX
                                      if t.endswith("macos")])
    def test_macho_symbols_wear_an_underscore(self, backend, target, tmp_path):
        """Mach-O's C ABI prefixes every symbol, at the definition AND the call."""
        text = _asm(tmp_path, backend, target)
        assert "_asmpython_main:" in text, "the entry point is not prefixed"
        assert "_add:" in text, "a defined symbol is not prefixed"
        # THE CALL SITE TOO, or the call resolves to nothing. Read off the
        # branch instruction rather than by position: the definition of `add`
        # precedes the call to it, so "before the label" finds the wrong half.
        call = [l for l in text.splitlines()
                if l.lstrip().startswith(("call", "bl "))]
        assert call, "no call instruction in the output"
        assert all("_add" in l for l in call), f"call site not prefixed: {call}"

    @harness.cases("backend,target", [(b, t) for b, t, _ in MATRIX
                                      if t.endswith("macos")])
    def test_macho_has_no_elf_directives(self, backend, target, tmp_path):
        """`.type`, `.size` and `.note.GNU-stack` are each a hard error there."""
        text = _asm(tmp_path, backend, target)
        for directive in (".type", ".size", ".note.GNU-stack"):
            assert directive not in text, f"{directive} survives into Mach-O"

    @harness.cases("backend,target", [("x86-64", "x86_64-linux"),
                                      ("arm64", "aarch64-linux")])
    def test_elf_still_declares_its_symbols(self, backend, target, tmp_path):
        """The other half of the fix: ELF must not have LOST anything."""
        text = _asm(tmp_path, backend, target)
        assert ".type" in text and ".size" in text
        assert "asmpython_main:" in text and "_asmpython_main:" not in text

    def test_coff_uses_its_own_definition_directive(self, tmp_path):
        """`.def/.scl/.endef`, and NOT the ELF pair.

        COFF's `.def` line carries a `.type 32;` of its own, so the test is
        that `.type x, @function` is absent -- not that the four characters
        `.type` are.
        """
        text = _asm(tmp_path, "x86-64", "x86_64-windows")
        assert ".def" in text and ".endef" in text
        assert "@function" not in text and ".size" not in text

    @harness.cases("backend,a,b", [
        ("x86-64", "x86_64-linux", "x86_64-macos"),
        ("x86-64", "x86_64-linux", "x86_64-windows"),
        ("arm64", "aarch64-linux", "aarch64-macos"),
    ])
    def test_two_formats_are_not_the_same_text(self, backend, a, b, tmp_path):
        """THE ASSERTION THAT WOULD HAVE CAUGHT IT.

        Byte-identical output for two object formats is the whole bug, and it
        is checkable without knowing which directive is wrong.
        """
        assert _asm(tmp_path, backend, a) != _asm(tmp_path, backend, b)


@harness.skip_if(not HAS_CLANG, reason="no clang to assemble with")
class TestItActuallyAssembles:
    """clang cross-assembles all three formats from any host, so this runs
    everywhere rather than only on the platform it describes."""

    @harness.cases("backend,target,triple", MATRIX)
    def test_the_output_assembles(self, backend, target, triple, tmp_path):
        source = tmp_path / "out.s"
        source.write_text(_asm(tmp_path, backend, target), encoding="utf-8")
        done = subprocess.run(
            ["clang", "-target", triple, "-c", str(source),
             "-o", str(tmp_path / "out.o")],
            capture_output=True, text=True)
        assert done.returncode == 0, (
            f"{backend}/{target} did not assemble as {triple}:\n{done.stderr}")
