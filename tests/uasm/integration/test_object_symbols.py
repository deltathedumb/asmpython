"""A global is defined under the name the code uses -- in every format.

THE BUG THIS EXISTS FOR. A global's name was spelled in two places by two
rules: the definition applied `dialect.symbol_prefix` and `GLOBAL_ADDR` did
not. On ELF and COFF the prefix is empty, so the two agreed by accident. On
Mach-O the program defined `___rodata0` and referenced `__rodata0`.

WHAT MADE IT INVISIBLE. The mismatch does not fail to link. It turns a defined
symbol into an undefined one, and an undefined symbol resolves to address
zero -- so AArch64 computed every string constant's address from page zero and
said nothing at all. x86-64 noticed only by accident, because a four-gigabyte
displacement does not fit its 32-bit field.

WHY IT IS AN INTEGRATION TEST. The claim needs a program that HAS globals, and
in this compiler that means one that uses a string -- which pulls in the object
runtime. The unit-level format tests compile a two-function arithmetic program
with no globals at all, so the invariant is unfalsifiable there.

THE ASSERTION IS NOT "IT LINKS", which would need a linker for each target.
It is that no name the compiler invented appears in the object's undefined
list, which is the same fact and is readable anywhere.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from tests import harness

from uasm.backend import get, load_builtin
from uasm.target import get as get_target

from .test_endtoend import ALL_PROGRAMS, compile_module

#: Every machine backend and the targets it serves. Written out rather than
#: derived so that a target added without an object writer fails here.
PAIRS = [("x86-64", "x86_64-linux"), ("x86-64", "x86_64-windows"),
         ("x86-64", "x86_64-macos"), ("arm64", "aarch64-linux"),
         ("arm64", "aarch64-none"), ("arm64", "aarch64-macos")]

#: A program with string constants, so the module actually declares globals.
PROGRAM = "class_basics"


#: THE COMPILED MODULE, BUILT ONCE. Compiling a program with classes pulls in
#: the whole object runtime and takes seconds; emitting it for a target takes
#: a fraction of one. Six pairs times two tests was twelve compilations of the
#: same program, which is minutes of suite time to answer one question.
_MODULE = None

#: AT IMPORT, not inside the helper. `get(backend)` is evaluated before the
#: module argument to `emit`, so a registry loaded lazily by that helper is
#: loaded one call too late and every backend name is unknown.
load_builtin()


def _module(tmp_path):
    global _MODULE
    if _MODULE is None:
        source = textwrap.dedent(ALL_PROGRAMS[PROGRAM]).strip() + "\n"
        _MODULE = compile_module(source, tmp_path, True)
    return _MODULE


@harness.needs("llvm-aarch64")
@harness.cases("backend,target", PAIRS)
class TestNoCompilerMadeNameIsLeftUndefined:

    def _object(self, backend, target, tmp_path) -> Path:
        (name, body), = get(backend).emit(
            _module(tmp_path), get_target(target)).items()
        path = tmp_path / name
        path.write_bytes(body)
        return path

    def test_the_globals_are_all_defined(self, backend, target, tmp_path):
        path = self._object(backend, target, tmp_path)
        out = subprocess.run(["llvm-nm", "-u", str(path)],
                             capture_output=True, text=True, check=True).stdout
        undefined = {line.strip() for line in out.splitlines() if line.strip()}
        # THE RUNTIME'S OWN NAMES ARE MEANT TO BE UNDEFINED: `apy_print` and
        # the rest are C that the linker supplies. A constant this compiler
        # invented is not, and every one of them is called `__rodata<n>`.
        leaked = sorted(n for n in undefined if "rodata" in n)
        assert not leaked, (
            f"{backend}/{target} references {len(leaked)} of its own globals "
            f"as undefined symbols, starting with {leaked[0]!r}")

    def test_the_object_declares_some_globals_at_all(self, backend, target,
                                                     tmp_path):
        """Otherwise the check above passes by having nothing to check.

        The unit-level format tests compile a program with no globals, which
        is exactly how this invariant went unnoticed for as long as it did.
        """
        path = self._object(backend, target, tmp_path)
        out = subprocess.run(["llvm-nm", "--defined-only", str(path)],
                             capture_output=True, text=True, check=True).stdout
        assert sum(1 for line in out.splitlines() if "rodata" in line) > 10, (
            "this program declares no globals, so the test proves nothing")
