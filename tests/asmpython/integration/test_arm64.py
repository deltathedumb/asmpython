"""The AArch64 backend, executed.

A third backend is how you find out whether the shared parts are shared.
`liveness` and `regalloc` had been written against one machine and used by
one machine, which proves nothing about either; this backend uses them
unchanged, on a machine that differs in every way that usually matters --
three-operand arithmetic, no memory operands, no 64-bit immediate, no
remainder instruction, and a stack pointer that must stay 16-byte aligned at
all times rather than only at a call.

RUNNING IT NEEDS NO ARM HARDWARE. The target is bare metal, so
`qemu-system-aarch64 -M virt -kernel` boots the image directly with no guest
OS, and output arrives over the PL011 UART. Everything here therefore
executes real AArch64 instructions and compares the result against CPython,
exactly as the x86-64 tests do -- an assembly-inspection test would prove
only that the emitter is self-consistent.

The whole suite skips when the toolchain or QEMU is absent, rather than
failing: neither is a normal thing to have, and a red suite people are told
to ignore is worse than a smaller green one.
"""
from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source

from . import aarch64

pytestmark = pytest.mark.skipif(not aarch64.AVAILABLE, reason=aarch64.REASON)


def build_and_run(src: str, tmp_path: Path, *, optimise: bool = False) -> list[str]:
    """Compile to a bare-metal AArch64 image and execute it under QEMU."""
    path = tmp_path / "prog.py"
    path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    exe = tmp_path / "prog.elf"

    with aarch64.on_path():
        sink = DiagnosticSink()
        result = compile_source(Options(
            source=path, output=exe, backend="arm64", link=True,
            optimise=optimise, workdir=tmp_path / "work",
            target=target_registry.get("aarch64-none")), sink)
        assert result.ok, [d.message for d in sink.diagnostics]

    return aarch64.run_image(result.program)


def cpython_lines(src: str) -> list[str]:
    out: list[str] = []
    ns = {"print": lambda *a: out.append(
        " ".join(f"{x:f}" if isinstance(x, float) else str(x) for x in a))}
    exec(compile(textwrap.dedent(src).strip() + "\n", "<t>", "exec"), ns)
    ns["main"]()
    return out


PROGRAMS = {
    "arithmetic": """
        def main() -> int:
            a: int = 17
            b: int = 5
            print(a + b, a - b, a * b)
            print(a // b, a % b)
            print(-a // b, a // -b, -a % b, a % -b)
            return 0
    """,
    "bitwise_and_shifts": """
        def main() -> int:
            a: int = 12
            b: int = 10
            print(a & b, a | b, a ^ b)
            print(a << 2, a >> 2, -a >> 1)
            return 0
    """,
    "control_flow": """
        def main() -> int:
            total: int = 0
            for i in range(10):
                if i % 3 == 0:
                    continue
                elif i == 8:
                    break
                else:
                    total += i
            print(total)
            n: int = 0
            while n < 5:
                n += 1
            print(n)
            down: int = 0
            for k in range(5, 0, -1):
                down += k
            print(down)
            return 0
    """,
    "recursion": """
        def fib(n: int) -> int:
            if n < 2:
                return n
            else:
                return fib(n - 1) + fib(n - 2)

        def main() -> int:
            print(fib(15))
            return 0
    """,
    "many_arguments": """
        def wide(a: int, b: float, c: int, d: int, e: float, f: int,
                 g: int, h: float, i: int, j: int) -> int:
            return a + int(b) + c + d + int(e) + f + g + int(h) + i + j

        def main() -> int:
            print(wide(1, 2.5, 3, 4, 5.5, 6, 7, 8.5, 9, 10))
            print(wide(-1, -2.5, -3, -4, -5.5, -6, -7, -8.5, -9, -10))
            return 0
    """,
    "floats": """
        def main() -> int:
            x: float = 7.5
            y: float = 2.0
            print(x + y, x - y, x * y, x / y)
            print(x % y, x // y, -x)
            print(2.0 ** 5, float(3) / 2.0)
            print(-7.5 % 2.0, 7.5 // -2.0)
            return 0
    """,
    "comparisons": """
        def main() -> int:
            a: int = 5
            b: int = 7
            x: float = 1.5
            print(int(a < b), int(a > b), int(a == 5), int(a != b))
            print(int(x < 2.0), int(x >= 1.5), int(x != x))
            print(int(1 < a < 10), int(a > 0 and b > 0), int(a < 0 or b > 0))
            return 0
    """,
    "conversions": """
        def main() -> int:
            print(int(2.7), int(-2.7))
            print(int(bool(2)), int(bool(0)), int(bool(2.5)))
            print(float(3), float(-7))
            return 0
    """,
    "print_arity": """
        def main() -> int:
            print(1, 2)
            print(1, 2, 3)
            print()
            print(7)
            return 0
    """,
}


@pytest.mark.parametrize("name", sorted(PROGRAMS))
def test_matches_cpython(name, tmp_path):
    src = PROGRAMS[name]
    assert build_and_run(src, tmp_path) == cpython_lines(src), src


@pytest.mark.parametrize("name", ["arithmetic", "control_flow", "floats",
                                  "many_arguments"])
def test_matches_cpython_optimised(name, tmp_path):
    src = PROGRAMS[name]
    assert build_and_run(src, tmp_path, optimise=True) == cpython_lines(src)


class TestTheHardParts:
    """Each of these is somewhere a previous backend was silently wrong."""

    def test_narrow_widths_wrap(self, tmp_path):
        """x86-64 computed narrow types at 64 bits and kept the answer.
        AArch64 registers are 64-bit too, so the same trap is here."""
        from io import StringIO
        from asmpython.ir import verify
        from asmpython.ir.interpreter import Interpreter
        from asmpython.ir.printer import parse_module
        ir = """\
module narrow

export func main() -> i64 {
entry:
    %0 = i8.const 127
    %1 = i8.const 1
    %2 = i8.add %0, %1
    %3 = i64.extend %2
    ret %3
}
"""
        module = parse_module(ir)
        verify(module)
        assert Interpreter(module, out=StringIO()).run("main") == -128

        from asmpython.backend import get as get_backend, load_builtin
        load_builtin()
        asm = get_backend("arm64").emit(
            module, target_registry.get("aarch64-none"))["out.s"].decode()
        assert "sxtb" in asm, "an i8 result must be narrowed back, not kept"

    def test_a_value_live_across_a_float_remainder(self, tmp_path):
        """Float `%` becomes a call to fmod, which the shared liveness cannot
        see. On x86-64 that put a live value in a volatile and destroyed it."""
        src = """
            def main() -> int:
                keep: int = 1234
                a: float = -7.5
                b: float = 2.0
                print(a % b)
                print(keep)
                return 0
        """
        assert build_and_run(src, tmp_path) == cpython_lines(src)

    def test_arguments_are_a_parallel_assignment(self, tmp_path):
        """Emitting argument moves in order collapses two arguments into one
        whenever a destination is also a source."""
        src = """
            def sub4(a: int, b: int, c: int, d: int) -> int:
                return a - b - c - d

            def main() -> int:
                w: int = 100
                x: int = 20
                y: int = 3
                z: int = 1
                print(sub4(w, x, y, z))
                print(sub4(z, y, x, w))
                return 0
        """
        assert build_and_run(src, tmp_path) == cpython_lines(src)

    def test_register_pressure_forces_spills(self, tmp_path):
        """More live values than registers. Spilling is where an allocator
        goes wrong, and a small program never reaches it."""
        names = [f"v{i}" for i in range(30)]
        lines = ["def main() -> int:"]
        lines += [f"    {n}: int = {i * 7 + 1}" for i, n in enumerate(names)]
        lines.append("    print(" + " + ".join(names) + ")")
        lines.append("    return 0")
        src = "\n".join(lines)
        assert build_and_run(src, tmp_path) == cpython_lines(src)


class TestTheFloatFormatter:
    """The runtime formats floats itself, so the formatter is under test too.

    Newlib is in the toolchain and would give a real `printf`, but it faults:
    its `snprintf` takes an alignment fault at EL1 as soon as malloc hands it
    a block. Sixty lines whose output can be checked directly beat chasing
    that, and this is the check.
    """

    def test_the_float_formatter_matches_printf(self, tmp_path):
        """Compiled for the HOST and compared against its printf, over the
        ties that a naive round-half-up gets wrong."""
        host_cc = shutil.which("gcc") or shutil.which("cc")
        if not host_cc:
            pytest.skip("no host C compiler")

        from asmpython.link.baremetal import RUNTIME_C, UART_ADDRESS
        runtime = RUNTIME_C % {"uart": UART_ADDRESS}
        # Only the formatter is wanted; the UART write becomes a buffer write.
        runtime = runtime.replace(
            "int putchar(int c) { *UART = (unsigned int)(unsigned char)c; "
            "return c; }",
            "extern char out[]; extern int outn;\n"
            "int putchar(int c) { out[outn++] = (char)c; return c; }")
        (tmp_path / "rt.c").write_text(runtime, encoding="utf-8")
        (tmp_path / "main.c").write_text(r"""
#include <stdio.h>
#include <string.h>
#include <stdint.h>
char out[64]; int outn;
void put_float(double);
static uint64_t s = 88172645463325252ULL;
static uint64_t rnd(void){ s^=s<<13; s^=s>>7; s^=s<<17; return s; }
int main(void) {
    double fixed[] = {0.0, -0.0, 0.5, 1.5, 2.5, 3.75, -3.75, 0.0000005,
                      0.0000015, 99.9999995, 1.0/3.0, 1e6, 123456.789, -0.1};
    long bad = 0, n = 0;
    char theirs[64];
    for (size_t i = 0; i < sizeof fixed / sizeof *fixed; i++) {
        outn = 0; put_float(fixed[i]); out[outn] = 0;
        snprintf(theirs, sizeof theirs, "%f", fixed[i]);
        n++; if (strcmp(out, theirs)) { bad++;
            if (bad < 5) printf("MISMATCH %.17g mine=%s printf=%s\n",
                                fixed[i], out, theirs); }
    }
    for (long i = 0; i < 50000; i++) {
        double v = (double)(int64_t)(rnd() % 2000001 - 1000000)
                 / (double)(1 + rnd() % 10000);
        outn = 0; put_float(v); out[outn] = 0;
        snprintf(theirs, sizeof theirs, "%f", v);
        n++; if (strcmp(out, theirs)) { bad++;
            if (bad < 5) printf("MISMATCH %.17g mine=%s printf=%s\n",
                                v, out, theirs); }
    }
    printf("%ld compared, %ld bad\n", n, bad);
    return bad != 0;
}
""", encoding="utf-8")
        exe = tmp_path / "fmt.exe"
        built = subprocess.run(
            [host_cc, "-O2", "-o", str(exe), str(tmp_path / "rt.c"),
             str(tmp_path / "main.c")],
            capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        assert ran.returncode == 0, ran.stdout
        assert "0 bad" in ran.stdout, ran.stdout


@pytest.mark.parametrize("seed", range(12))
def test_generated_programs_match_cpython(seed, tmp_path):
    """The differential fuzzer's programs, on a third machine.

    The generator knows nothing about AArch64 -- it emits the same Python it
    always did, and the comparison is against CPython as always. That is the
    point: a backend is right when the same evidence that convinced you about
    the other two applies to it unchanged.
    """
    from tests.asmpython.integration.test_differential import (
        ProgramGenerator, cpython_output)
    src = ProgramGenerator(seed).program()
    assert build_and_run(src, tmp_path) == cpython_output(src), src
