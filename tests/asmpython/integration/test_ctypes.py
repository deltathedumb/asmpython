"""`ctypes`: calling a native library from a compiled program.

THE POINT IS WHAT IT DID NOT COST. The obvious implementation is
`dlopen`/`dlsym` -- a FOURTH and FIFTH platform function, against a floor that
stage 2 of `docs/INERT-RUNTIME.md` spent its whole argument getting down to
three. None was added, because none was needed: the C backend already emits
`extern double sqrt(double);` for any external the IR declares and does not
define, and the toolchain resolves it. That was measured with hand-written IR
before a line of this was designed.

So `ctypes.CDLL("m")` is not a load, it is a promise to the linker, and
`libm.sqrt(...)` is an ordinary `Op.CALL`. Nothing happens at run time that
would not happen in a C program calling the same function -- which is also
why these tests check the OUTPUT rather than any machinery: if the answer is
right, the ABI was right.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from tests import harness
from tests.harness import snapshot

SRC = snapshot.current(Path(__file__).resolve().parents[3])


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "asmpython", *args],
                          capture_output=True, text=True, env=env)


def write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "prog.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def build_and_run(tmp_path: Path, source: str) -> subprocess.CompletedProcess:
    out = tmp_path / "prog.exe"
    built = _cli("build", str(write(tmp_path, source)), "--backend", "c",
                 "-o", str(out), "--workdir", str(tmp_path / "wd"))
    assert built.returncode == 0, built.stdout + built.stderr
    return subprocess.run([str(out)], capture_output=True, text=True)


def refused(tmp_path: Path, source: str) -> str:
    r = _cli("build", str(write(tmp_path, source)), "--emit-ir",
             "-o", str(tmp_path / "p.ir"), "--workdir", str(tmp_path / "wd"))
    assert r.returncode != 0, "expected a diagnostic"
    return r.stdout + r.stderr


class TestItCallsTheLibrary:
    @harness.needs("gcc")
    def test_a_double_returning_function(self, tmp_path):
        """`sqrt` out of libm, by value, in and out.

        A float argument passed where the callee wants a double is the ctypes
        mistake that does not fail -- it produces a plausible wrong number --
        so the assertion is the exact digits.
        """
        got = build_and_run(tmp_path, """\
            import ctypes
            libm = ctypes.CDLL("m")
            libm.sqrt.restype = ctypes.c_double
            libm.sqrt.argtypes = [ctypes.c_double]

            def main() -> int:
                print(libm.sqrt(9.0))
                print(libm.sqrt(2.0))
                return 0
        """)
        assert got.returncode == 0, got.stderr
        assert got.stdout.split("\n")[:2] == ["3.0", "1.4142135623730951"]

    @harness.needs("gcc")
    def test_two_arguments_and_a_narrow_integer(self, tmp_path):
        """`c_int32` is 32 bits and Python's int is 64. Getting that wrong
        does not raise, it truncates -- which is the whole reason `argtypes`
        is required here rather than guessed."""
        got = build_and_run(tmp_path, """\
            from ctypes import CDLL, c_double, c_int32

            libm = CDLL("m")
            libm.pow.restype = c_double
            libm.pow.argtypes = [c_double, c_double]
            libm.abs.restype = c_int32
            libm.abs.argtypes = [c_int32]

            def main() -> int:
                print(libm.pow(2.0, 10.0))
                print(libm.abs(-42))
                return 0
        """)
        assert got.returncode == 0, got.stderr
        assert got.stdout.split("\n")[:2] == ["1024.0", "42"]

    @harness.needs("gcc")
    def test_the_library_reaches_the_linker_without_a_flag(self, tmp_path):
        """`CDLL("m")` is the whole declaration. Needing `--link-input=-lm` as
        well would mean saying the same thing twice, in two places that can
        disagree."""
        got = build_and_run(tmp_path, """\
            import ctypes
            libm = ctypes.CDLL("libm.so.6")
            libm.sqrt.restype = ctypes.c_double
            libm.sqrt.argtypes = [ctypes.c_double]

            def main() -> int:
                print(libm.sqrt(16.0))
                return 0
        """)
        assert got.returncode == 0, got.stderr
        assert got.stdout.startswith("4.0")


class TestTheDeclarationsAreNotCode:
    """They describe the build, and emitting them was three separate bugs."""

    def test_a_declaration_does_not_make_the_module_the_entry(self, tmp_path):
        """A module whose only top-level statements are ctypes declarations
        has no top level. Counting them made `<module>` the entry, so `main`
        was renamed out of the way and never called -- and the program
        compiled, linked, and printed nothing at all."""
        r = _cli("build", str(write(tmp_path, """\
            import ctypes
            libm = ctypes.CDLL("m")
            libm.sqrt.restype = ctypes.c_double
            libm.sqrt.argtypes = [ctypes.c_double]

            def main() -> int:
                print(libm.sqrt(9.0))
                return 0
        """)), "--emit-ir", "-o", str(tmp_path / "p.ir"),
                 "--workdir", str(tmp_path / "wd"))
        assert r.returncode == 0, r.stdout + r.stderr
        text = (tmp_path / "p.ir").read_text(encoding="utf-8")
        assert "export func main()" in text
        # Renamed to `py_main` only when a module entry exists, which is the
        # symptom this test exists for.
        assert "py_main" not in text, text[:400]

    def test_the_native_call_is_a_direct_call(self, tmp_path):
        """No load, no lookup, no runtime machinery -- one `call` to a symbol
        the module declares and does not define."""
        r = _cli("build", str(write(tmp_path, """\
            import ctypes
            libm = ctypes.CDLL("m")
            libm.sqrt.restype = ctypes.c_double
            libm.sqrt.argtypes = [ctypes.c_double]

            def main() -> int:
                print(libm.sqrt(9.0))
                return 0
        """)), "--emit-ir", "-o", str(tmp_path / "p.ir"),
                 "--workdir", str(tmp_path / "wd"))
        assert r.returncode == 0, r.stdout + r.stderr
        text = (tmp_path / "p.ir").read_text(encoding="utf-8")
        assert "func sqrt(%0: f64) -> f64 external" in text
        assert "f64.call @sqrt" in text

    def test_no_platform_function_was_added(self):
        """The claim this whole feature is measured against."""
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link.platform import FLOOR
            assert set(FLOOR) == {"plat_write", "plat_exit", "plat_heap"}, FLOOR
        finally:
            del sys.path[0]


class TestWhatItRefuses:
    """Each refusal is a thing that cannot work this way, said plainly."""

    def test_a_computed_library_name(self, tmp_path):
        """The one case `dlopen` would buy. There is no symbol to hand the
        linker, so it is refused rather than half-supported."""
        assert "E0124" in refused(tmp_path, """\
            import ctypes
            name = "m"
            lib = ctypes.CDLL(name)

            def main() -> int:
                return 0
        """)

    def test_a_call_with_no_argtypes(self, tmp_path):
        """STRICTER THAN CPYTHON, deliberately: there a missing `argtypes`
        means guess from the values, and guessing is how a ctypes program
        corrupts a stack."""
        assert "E0125" in refused(tmp_path, """\
            import ctypes
            libm = ctypes.CDLL("m")

            def main() -> int:
                print(libm.sqrt(9.0))
                return 0
        """)

    def test_a_ctypes_name_this_frontend_does_not_have(self, tmp_path):
        """Structs, arrays, `byref` and callbacks are not here yet, and a
        program using one should hear that rather than `undefined name`."""
        assert "E0128" in refused(tmp_path, """\
            from ctypes import Structure

            def main() -> int:
                return 0
        """)

    def test_a_library_used_as_a_value(self, tmp_path):
        """`libm` is a compile-time name and there is nothing to pass around,
        so reading one is refused where it is written."""
        assert "E0127" in refused(tmp_path, """\
            import ctypes
            libm = ctypes.CDLL("m")

            def main() -> int:
                x = libm
                return 0
        """)
