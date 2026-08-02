"""End-to-end: source through every path, compared against CPython.

CPython is the oracle. For each program, four things must agree:

    1. CPython running it directly
    2. the reference interpreter on the unoptimised IR
    3. the reference interpreter on the OPTIMISED IR
    4. the C backend's output, compiled and run

(3) is what catches a pass that changes meaning, which no amount of testing a
pass in isolation will find -- a pass can be individually correct and still
wrong in combination. (4) is what catches a backend that disagrees with the
interpreter, which is the only way to know either is right.

The C compilation is skipped if no C compiler is present, rather than failing:
a machine without gcc can still run everything else, and turning that into a
red suite trains people to ignore it.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from io import StringIO
from pathlib import Path

import pytest

from apc.diagnostics import DiagnosticSink, SourceFile
from apc.driver import Options, compile_source
from apc.ir.interpreter import Interpreter
from apc.ir.printer import parse_module, print_module

HAS_CC = shutil.which("gcc") or shutil.which("cc")

#: The host's object format decides which assembler directives are legal and
#: which ABI the system libraries expect. Guessing either produces output that
#: assembles on one platform and is silently wrong on the other.
_HOST_OBJECT_FORMAT = "coff" if sys.platform == "win32" else "elf"
_HOST_TARGET_NAME = ("x86_64-windows" if sys.platform == "win32"
                     else "x86_64-linux")

#: The runtime the frontend's `print` calls resolve against, plus an entry
#: point. A frontend chooses its own runtime; the IR has no I/O opcodes.
_RUNTIME_C = (
    "#include <stdio.h>\n"
    "#include <stdint.h>\n"
    'void print_int(int64_t v)  { printf("%lld\\n", (long long)v); }\n'
    'void print_float(double v) { printf("%f\\n", v); }\n'
    "extern int64_t main_ir(void);\n"
    "int main(void) { return (int)main_ir(); }\n"
)

PROGRAMS = {
    "arithmetic": """
        def main() -> int:
            a: int = 17
            b: int = 5
            print(a + b)
            print(a - b)
            print(a * b)
            print(a // b)
            print(a % b)
            print(-a // b)
            print(a // -b)
            print(-a % b)
            print(a % -b)
            return 0
    """,
    "loops": """
        def main() -> int:
            total: int = 0
            for i in range(1, 11):
                total = total + i
            print(total)
            n: int = 0
            while n < 5:
                n = n + 1
            print(n)
            return total
    """,
    "calls": """
        def square(x: int) -> int:
            return x * x

        def sum_squares(n: int) -> int:
            total: int = 0
            for i in range(n):
                total = total + square(i)
            return total

        def main() -> int:
            print(sum_squares(5))
            print(square(7))
            return 0
    """,
    "conditionals": """
        def classify(n: int) -> int:
            if n < 0:
                return -1
            if n == 0:
                return 0
            return 1

        def main() -> int:
            print(classify(-5))
            print(classify(0))
            print(classify(5))
            return 0
    """,
    "short_circuit": """
        def main() -> int:
            a: int = 5
            b: int = 0
            if a > 0 and b == 0:
                print(1)
            if a < 0 or b == 0:
                print(2)
            if 1 < a < 10:
                print(3)
            return 0
    """,
    "bitwise": """
        def main() -> int:
            a: int = 12
            b: int = 10
            print(a & b)
            print(a | b)
            print(a ^ b)
            print(a << 2)
            print(a >> 2)
            return 0
    """,
    "nested_control": """
        def main() -> int:
            count: int = 0
            for i in range(4):
                for j in range(4):
                    if i < j:
                        count = count + 1
            print(count)
            return count
    """,
    "power": """
        def main() -> int:
            print(2 ** 10)
            print(3 ** 0)
            print(7 ** 1)
            print((-3) ** 3)
            print(2 ** 5 + 1)
            return 0
    """,
    "break_continue": """
        def main() -> int:
            total: int = 0
            for i in range(10):
                if i == 5:
                    break
                total = total + i
            print(total)
            odd: int = 0
            for j in range(10):
                if j % 2 == 0:
                    continue
                odd = odd + j
            print(odd)
            n: int = 0
            while True:
                n = n + 1
                if n > 3:
                    break
            print(n)
            inner: int = 0
            for a in range(4):
                for b in range(4):
                    if b == 2:
                        break
                    inner = inner + 1
            print(inner)
            return 0
    """,
    "descending_range": """
        def main() -> int:
            total: int = 0
            for k in range(5, 0, -1):
                total = total + k
            print(total)
            stepped: int = 0
            for m in range(0, 10, 3):
                stepped = stepped + m
            print(stepped)
            return total
    """,
    "conversions": """
        def main() -> int:
            print(int(2.7))
            print(int(-2.7))
            print(int(bool(2)))
            print(int(bool(0)))
            print(int(bool(2.5)))
            print(int(float(3)))
            return 0
    """,
}

#: Float printing is the one place the frontend does not match CPython: the
#: runtime prints C's `%f` (`32.000000`), not Python's repr (`32.0`). The
#: oracle stays CPython for the VALUE, so the comparison formats the same way
#: rather than pretending the difference is not there.
FLOAT_PROGRAMS = {
    "float_arithmetic": """
        def main() -> int:
            a: float = 7.5
            b: float = 2.0
            print(a + b)
            print(a - b)
            print(a * b)
            print(a / b)
            print(2.0 ** 5)
            print(float(3) / 2.0)
            return 0
    """,
}


def _render(value) -> str:
    """Format one printed value the way the runtime does.

    Only floats differ: the runtime uses C's `%f`. Everything else is str().
    """
    return f"{value:f}" if isinstance(value, float) else str(value)


def cpython_output(src: str) -> tuple[list[str], int]:
    captured: list[str] = []
    namespace: dict = {
        "print": lambda *a: captured.append(" ".join(_render(x) for x in a))}
    exec(compile(src, "<test>", "exec"), namespace)
    return captured, namespace["main"]()


def compile_module(src: str, tmp_path: Path, optimise: bool):
    path = tmp_path / "prog.py"
    path.write_text(src, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path, optimise=optimise), sink)
    assert result.ok, f"compilation failed:\n{[d.message for d in sink.diagnostics]}"
    return result.module


def interpret(module) -> tuple[list[str], int]:
    out = StringIO()
    value = Interpreter(module, out=out).run("main")
    return out.getvalue().split("\n")[:-1] if out.getvalue() else [], value


ALL_PROGRAMS = {**PROGRAMS, **FLOAT_PROGRAMS}


@pytest.mark.parametrize("name", sorted(ALL_PROGRAMS))
class TestAgreement:
    def program(self, name: str) -> str:
        return textwrap.dedent(ALL_PROGRAMS[name]).strip() + "\n"

    def test_unoptimised_matches_cpython(self, name, tmp_path):
        src = self.program(name)
        want_out, want_value = cpython_output(src)
        got_out, got_value = interpret(compile_module(src, tmp_path, False))
        assert got_out == want_out
        assert got_value == want_value

    def test_optimised_matches_cpython(self, name, tmp_path):
        """A pass that changes meaning shows up here and nowhere else."""
        src = self.program(name)
        want_out, want_value = cpython_output(src)
        got_out, got_value = interpret(compile_module(src, tmp_path, True))
        assert got_out == want_out
        assert got_value == want_value

    def test_ir_text_round_trips_and_still_runs(self, name, tmp_path):
        src = self.program(name)
        module = compile_module(src, tmp_path, True)
        text = print_module(module)
        assert print_module(parse_module(text)) == text
        want_out, want_value = cpython_output(src)
        got_out, got_value = interpret(parse_module(text))
        assert got_out == want_out and got_value == want_value

    @pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
    def test_x86_64_backend_matches_cpython(self, name, tmp_path):
        """Assemble the generated assembly and run it.

        This is the path that caught a real miscompilation: a loop counter
        passed as a call argument and read afterwards was placed in a
        caller-saved register, so the call destroyed it and the loop ended
        early. The interpreter was right, the backend was wrong, and only
        running both revealed which.
        """
        from apc.backend import Target, get, load_builtin
        from apc.backends.x86_64.emit import UnsupportedOperation
        load_builtin()
        src = self.program(name)
        want_out, want_value = cpython_output(src)
        module = compile_module(src, tmp_path, True)

        # The IR entry is `main`; C's main wraps it.
        module.function("main").name = "main_ir"
        target = Target(_HOST_TARGET_NAME, object_format=_HOST_OBJECT_FORMAT)
        try:
            asm = get("x86-64").emit(module, target)["out.s"]
        except UnsupportedOperation as exc:
            pytest.skip(f"backend does not implement this yet: {exc}")

        s_file = tmp_path / "out.s"
        s_file.write_bytes(asm)
        rt = tmp_path / "rt.c"
        rt.write_text(_RUNTIME_C, encoding="utf-8")
        exe = tmp_path / "out.exe"
        built = subprocess.run([HAS_CC, str(s_file), str(rt), "-o", str(exe)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        assert ran.stdout.split("\n")[:-1] == want_out
        assert ran.returncode == (want_value & 0xFF)

    @pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
    def test_c_backend_matches_cpython(self, name, tmp_path):
        from apc.backend import PORTABLE_C, get, load_builtin
        load_builtin()
        src = self.program(name)
        want_out, want_value = cpython_output(src)
        module = compile_module(src, tmp_path, True)

        c_file = tmp_path / "out.c"
        c_file.write_bytes(get("c").emit(module, PORTABLE_C)["out.c"])
        exe = tmp_path / "out.exe"
        built = subprocess.run([HAS_CC, str(c_file), "-o", str(exe)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        assert ran.stdout.split("\n")[:-1] == want_out
        assert ran.returncode == (want_value & 0xFF)


class TestDiagnostics:
    def compile_bad(self, src: str, tmp_path: Path):
        path = tmp_path / "bad.py"
        path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
        sink = DiagnosticSink()
        result = compile_source(Options(source=path), sink)
        return result, sink

    def test_reports_every_error_not_just_the_first(self, tmp_path):
        result, sink = self.compile_bad("""
            def main() -> int:
                a = undefined_one
                b = undefined_two
                c = undefined_three
                return 0
        """, tmp_path)
        assert not result.ok
        codes = [d.code for d in sink.diagnostics]
        assert codes.count("E0031") == 3

    def test_poisoning_reports_an_unknown_name_once(self, tmp_path):
        """`ghost + 1 + 2` is one error, not three."""
        _, sink = self.compile_bad("""
            def main() -> int:
                x = ghost + 1 + 2
                return 0
        """, tmp_path)
        assert len([d for d in sink.diagnostics if d.code == "E0031"]) == 1

    def test_narrowing_is_not_implicit(self, tmp_path):
        _, sink = self.compile_bad("""
            def main() -> int:
                f: float = 1.5
                n: int = f
                return 0
        """, tmp_path)
        assert any(d.code == "E0060" for d in sink.diagnostics)
        assert any("narrowing is never implicit" in h
                   for d in sink.diagnostics for h in d.helps)

    def test_widening_is_allowed(self, tmp_path):
        path = tmp_path / "ok.py"
        path.write_text(textwrap.dedent("""
            def main() -> int:
                n: int = 3
                f: float = n
                return 0
        """).strip() + "\n", encoding="utf-8")
        sink = DiagnosticSink()
        assert compile_source(Options(source=path), sink).ok

    def test_wrong_argument_count_points_at_the_definition(self, tmp_path):
        _, sink = self.compile_bad("""
            def two(a: int, b: int) -> int:
                return a

            def main() -> int:
                return two(1)
        """, tmp_path)
        d = next(d for d in sink.diagnostics if d.code == "E0053")
        assert len(d.labels) >= 2, "should point at the call AND the definition"

    def test_syntax_error_has_a_position(self, tmp_path):
        _, sink = self.compile_bad("def main() -> int:\n    return (", tmp_path)
        assert sink.failed
        assert sink.diagnostics[0].has_location

    def test_missing_main_is_reported(self, tmp_path):
        _, sink = self.compile_bad("""
            def helper() -> int:
                return 1
        """, tmp_path)
        assert any(d.code == "E0003" for d in sink.diagnostics)

    def test_redeclaring_with_another_type_is_rejected(self, tmp_path):
        _, sink = self.compile_bad("""
            def main() -> int:
                x: int = 1
                x: float = 2.0
                return 0
        """, tmp_path)
        assert any(d.code == "E0030" for d in sink.diagnostics)


class TestPipelineContract:
    def test_emit_ir_stops_before_the_backend(self, tmp_path):
        path = tmp_path / "p.py"
        path.write_text("def main() -> int:\n    return 1\n", encoding="utf-8")
        sink = DiagnosticSink()
        result = compile_source(Options(source=path, emit_ir=True), sink)
        assert result.ir_text is not None
        assert result.artifacts == {}

    def test_unknown_frontend_is_a_clean_error(self, tmp_path):
        path = tmp_path / "p.xyz"
        path.write_text("nonsense", encoding="utf-8")
        sink = DiagnosticSink()
        result = compile_source(Options(source=path), sink)
        assert not result.ok
        assert any(d.code == "E9101" for d in sink.diagnostics)

    def test_missing_file_is_a_clean_error(self, tmp_path):
        sink = DiagnosticSink()
        result = compile_source(Options(source=tmp_path / "nope.py"), sink)
        assert not result.ok
        assert any(d.code == "E9100" for d in sink.diagnostics)
