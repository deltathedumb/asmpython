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

import math
import random
import shutil
import struct
import subprocess
import textwrap
from pathlib import Path

from tests import harness

from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source

from . import aarch64

#: Applied per test rather than as a module `pytestmark`, because the float
#: formatter below is ordinary C with no AArch64 in it and runs on the host.
#: Under a module-wide skip it ran only for whoever had a cross toolchain
#: installed -- and it is the check that the runtime prints Python's numbers.
needs_aarch64 = harness.skip_if(not aarch64.AVAILABLE, aarch64.REASON)


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
    """What CPython prints for the same program.

    Plain `str`, with no special case for floats. This used to render them
    with `f"{x:f}"` to match a runtime that printed C's six decimals -- which
    meant the comparison hid the disagreement instead of finding it, and
    `print(7.5 / 2.0)` passed while the backend printed `3.750000`.
    """
    out: list[str] = []
    ns = {"print": lambda *a: out.append(" ".join(str(x) for x in a))}
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


@needs_aarch64
@harness.cases("name", sorted(PROGRAMS))
def test_matches_cpython(name, tmp_path):
    src = PROGRAMS[name]
    assert build_and_run(src, tmp_path) == cpython_lines(src), src


@needs_aarch64
@harness.cases("name", ["arithmetic", "control_flow", "floats",
                                  "many_arguments"])
def test_matches_cpython_optimised(name, tmp_path):
    src = PROGRAMS[name]
    assert build_and_run(src, tmp_path, optimise=True) == cpython_lines(src)


@needs_aarch64
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


def _float_corpus() -> list[float]:
    """The doubles the bare-metal formatter is held to.

    Chosen rather than sampled, because uniformly random bit patterns are
    almost all enormous or minuscule and would never once exercise the fixed
    notation that ordinary programs print. Each group below is a place a float
    formatter is known to go wrong:
    """
    seen: dict[bytes, float] = {}

    def add(value: float) -> None:
        seen.setdefault(struct.pack("<d", value), value)

    for value in (0.0, -0.0, math.inf, -math.inf, math.nan, -math.nan):
        add(value)
    # Every power of ten in range, where the shortest digit string is a single
    # digit and the decimal exponent is one step from a notation boundary.
    for power in range(-323, 309):
        add(float(f"1e{power}"))
        add(-float(f"1e{power}"))
    # Every binade, plus the value just off each one: `mf == 0` is the case
    # where the gap below a double is half the gap above it, and a formatter
    # that treats the interval as symmetric prints one digit too many there.
    for power in range(-1074, 1024):
        add(math.ldexp(1.0, power))
        add(math.nextafter(math.ldexp(1.0, power), math.inf))
        add(math.nextafter(math.ldexp(1.0, power), 0.0))
    # The subnormals, whose exact decimal runs to 751 significant digits.
    for i in range(1, 2000):
        add(struct.unpack("<d", struct.pack("<Q", i))[0])
    # Values needing all seventeen digits, and the notation boundary itself:
    # 1e16 is the first value Python writes in exponent form, and `%g` would
    # switch somewhere else entirely.
    for value in (12345678901234567.0, 1.2345678901234568e+16, 9007199254740993.0,
                  1e16, 9999999999999998.0, 1e15, 1e-4, 1e-5, 0.0001, 0.00001,
                  1.7976931348623157e308, 2.2250738585072014e-308, 5e-324,
                  0.1, 0.2, 0.3, 1 / 3, 2 / 3, 1e22, 1e23):
        add(value)
        add(-value)
    # The ties. A decimal exactly between two doubles reads back as the one
    # with an even mantissa, and both ends of that rule are here.
    for i in range(1, 400):
        add(i / 2.0)
        add(i / 4.0)
        add(1.0 + i * 2.0 ** -52)
        add(float(i))
        add(float(i) / 10.0)

    rng = random.Random(20260806)
    while len(seen) < 200_000:
        add(struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0])
    return list(seen.values())


def _bits(value: float) -> int:
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _run_host_runtime(tmp_path: Path, main_c: str, lines: list[str]) -> list[str]:
    """Build the bare-metal runtime FOR THE HOST, feed it `lines`, return its
    output lines.

    The runtime's arithmetic and formatting are ordinary C with no AArch64 in
    them, so running them here rather than under QEMU turns a wrong digit into
    a two-second unit failure instead of something only whoever has a cross
    toolchain installed ever sees.

    Values travel as hexadecimal bit patterns in a file, in and out. Bits
    rather than decimal text because the point is to compare against CPython
    exactly, and a decimal round-trip through the C library is the very thing
    under test; a file rather than argv because there are hundreds of
    thousands of them.
    """
    host_cc = shutil.which("gcc") or shutil.which("cc")
    if not host_cc:
        harness.skip("no host C compiler")

    # `runtime_c()`, not the raw `RUNTIME_C`: the template carries a `@POW@`
    # placeholder for the source it shares with the hosted runtime, and the
    # raw text does not compile.
    from asmpython.link.baremetal import runtime_c, UART_ADDRESS
    runtime = runtime_c() % {"uart": UART_ADDRESS}
    # There is no UART on this machine; the write becomes a buffer write. The
    # buffer lives HERE, with the putchar that fills it, rather than in each
    # main.c -- a test that only calls arithmetic still links this putchar and
    # would otherwise have to declare a buffer it never uses.
    uart_putchar = ("int putchar(int c) { *UART = (unsigned int)"
                    "(unsigned char)c; return c; }")
    assert uart_putchar in runtime, (
        "the runtime's putchar is not where this expects it; a substitution "
        "that silently matches nothing leaves the UART write in place and "
        "these tests then fail at link with no hint of why")
    runtime = runtime.replace(
        uart_putchar,
        "static char captured[64]; static int captured_n;\n"
        "int putchar(int c) { captured[captured_n++] = (char)c; return c; }\n"
        "const char *captured_output(void) {\n"
        "    captured[captured_n] = 0; captured_n = 0; return captured; }")
    (tmp_path / "rt.c").write_text(runtime, encoding="utf-8")
    (tmp_path / "main.c").write_text(main_c, encoding="utf-8")

    exe = tmp_path / "hostrt.exe"
    # `-ffreestanding -fno-builtin` IS THE TEST, not a detail of it. Names like
    # `fmod` and `pow` are GCC builtins: given a hosted compile it will fold
    # them at compile time or emit a call that binds to libm, and the runtime's
    # own definition -- the thing under test -- is never reached. Measured, by
    # putting the old inexact `fmod` back and running the corpus below: 0 of
    # 100,000 pairs disagreed with libm without these flags and 46,262 with
    # them. A test that green-lights the implementation it is meant to check is
    # worse than no test, and this one did.
    #
    # `-ffreestanding` also happens to be what `BareMetalToolchain.link` really
    # passes, so this compiles the runtime the way the runtime is compiled.
    built = subprocess.run(
        [host_cc, "-O2", "-ffreestanding", "-fno-builtin", "-o", str(exe),
         str(tmp_path / "rt.c"), str(tmp_path / "main.c")],
        capture_output=True, text=True)
    assert built.returncode == 0, built.stderr

    (tmp_path / "in.txt").write_text("".join(l + "\n" for l in lines),
                                     encoding="ascii")
    ran = subprocess.run(
        [str(exe), str(tmp_path / "in.txt"), str(tmp_path / "out.txt")],
        capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    got = (tmp_path / "out.txt").read_text(encoding="ascii").split("\n")[:-1]
    assert len(got) == len(lines), f"{len(lines)} in, {len(got)} out"
    return got


class TestTheFloatFormatter:
    """The runtime formats floats itself, so the formatter is under test too.

    Newlib is in the toolchain and would give a real `printf`, but it faults:
    its `snprintf` takes an alignment fault at EL1 as soon as malloc hands it
    a block. A formatter whose output can be checked directly beats chasing
    that, and this is the check.
    """

    def test_the_float_formatter_matches_repr(self, tmp_path):
        """Compared against CPython's `repr`.

        Against `repr` and not against `printf`, because `repr` is what the
        other four execution paths print and printf cannot produce it: this
        used to assert agreement with `printf("%f")`, which is the six-decimal
        behaviour that made the AArch64 backend disagree with all of them.

        The values are chosen HERE and passed in as bit patterns, so what the
        formatter is measured against is `repr` itself and not a C
        reimplementation of Python's rules -- which would only prove that two
        reimplementations made the same mistake.
        """
        values = _float_corpus()
        assert len(values) >= 200_000
        got = _run_host_runtime(tmp_path, r"""
#include <stdio.h>
void put_float(double);
const char *captured_output(void);
int main(int argc, char **argv) {
    FILE *in = fopen(argv[1], "r"), *dest = fopen(argv[2], "w");
    if (!in || !dest) return 2;
    unsigned long long bits;
    while (fscanf(in, "%llx", &bits) == 1) {
        union { unsigned long long u; double d; } x;
        x.u = bits;
        put_float(x.d);
        fprintf(dest, "%s\n", captured_output());
    }
    return fclose(dest) != 0;
}
""", ["%016x" % _bits(v) for v in values])

        # Reported in bulk with the first few named. One mismatch is a bug and
        # a thousand is the same bug, but which values they are says which.
        bad = [(v, mine) for v, mine in zip(values, got) if mine != repr(v)]
        assert not bad, (
            f"{len(bad)} of {len(values)} disagree with repr; first: "
            + "; ".join(f"{v!r} (bits {_bits(v):#018x}) formatted as {mine!r}"
                        for v, mine in bad[:5]))


def _fmod_corpus() -> list[tuple[float, float]]:
    """The (dividend, divisor) pairs the runtime's `fmod` is held to."""
    pairs: list[tuple[float, float]] = []
    # The pair that found the inexact implementation: the generated program
    # for differential seed 15 computes `(-53.7228) % (-1.7785)`, and an
    # `a - trunc(a/b)*b` remainder is wrong in the 15th digit here.
    pairs += [(-53.7228, -1.7785), (-7.7786, 11.9616), (61.9250, -17.3664)]
    # Signs, in every combination, including the zero remainder that has to
    # keep the DIVIDEND's sign.
    for a in (6.0, -6.0, 0.0, -0.0, 7.5, -7.5):
        for b in (3.0, -3.0, 2.0, -2.0):
            pairs.append((a, b))
    # Not-a-number, infinity and division by zero, on both sides.
    for a in (1.0, math.inf, -math.inf, math.nan, 0.0):
        for b in (0.0, -0.0, math.inf, -math.inf, math.nan, 2.0):
            pairs.append((a, b))
    # A subnormal divisor against a normal dividend. This is the case that a
    # bit-at-a-time remainder gets wrong unless BOTH mantissas are normalised
    # first: one conditional subtract per bit removes almost nothing from a
    # dividend 2^1000 times the divisor.
    tiny = 5e-324
    for a in (1.0, 2.5, 1e-300, 2.2250738585072014e-308, 1.5e-323):
        for b in (tiny, 3 * tiny, 1e-320, 4.45e-323):
            pairs.append((a, b))
            pairs.append((-a, b))
    # |a| < |b|, |a| == |b|, and exact multiples, where the answer is a
    # signed zero or the dividend untouched.
    for a in (1.0, 1e300, 5e-324, 0.25):
        pairs += [(a, a), (a, -a), (a, a * 4), (a * 4, a), (a, math.inf)]

    rng = random.Random(4062026)

    def draw() -> float:
        # Three buckets, deliberately. Uniformly random bit patterns alone are
        # almost all astronomically large or small, so `%` between two of them
        # leaves by the `|a| < |b|` line without ever reaching the division;
        # and they are subnormal only about once in 2,000, which is not often
        # enough to rely on for the case that has already been wrong once.
        roll = rng.random()
        if roll < 0.4:
            return struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        if roll < 0.8:
            return rng.uniform(-1000.0, 1000.0) * 10.0 ** rng.randint(-8, 8)
        tiny = struct.unpack("<d", struct.pack(
            "<Q", rng.getrandbits(rng.randint(1, 53))))[0]
        return -tiny if rng.random() < 0.5 else tiny

    while len(pairs) < 100_000:
        pairs.append((draw(), draw()))
    return pairs


class TestTheFloatRemainder:
    """`fmod` is the runtime's, not libm's, so it is under test too.

    Every other execution path -- CPython, the IR interpreter, the C backend,
    the x86-64 backend -- ends up in libm's exact `fmod`. This one has no libm
    to call, so it is the only place where `%` can quietly compute a different
    number, and this is what stops it.
    """

    def test_the_float_remainder_matches_libm(self, tmp_path):
        """Compared bit for bit against `math.fmod`, which is libm's.

        Bit for bit and not `==`, because the whole failure mode is a result
        one ulp or so away from the right one -- which compares unequal and
        prints identically under any format short of `repr`.
        """
        pairs = _fmod_corpus()
        got = _run_host_runtime(tmp_path, r"""
#include <stdio.h>
double fmod(double, double);
int main(int argc, char **argv) {
    FILE *in = fopen(argv[1], "r"), *dest = fopen(argv[2], "w");
    if (!in || !dest) return 2;
    unsigned long long ab, bb;
    while (fscanf(in, "%llx %llx", &ab, &bb) == 2) {
        union { unsigned long long u; double d; } a, b, r;
        a.u = ab; b.u = bb;
        r.d = fmod(a.d, b.d);
        fprintf(dest, "%016llx\n", r.u);
    }
    return fclose(dest) != 0;
}
""", ["%016x %016x" % (_bits(a), _bits(b)) for a, b in pairs])

        bad = []
        for (a, b), mine in zip(pairs, got):
            mine_f = struct.unpack("<d", struct.pack("<Q", int(mine, 16)))[0]
            try:
                want = math.fmod(a, b)
            except ValueError:
                # `fmod(inf, y)` and `fmod(x, 0)` are domain errors to Python
                # but plain NaN results to C, so only NaN-ness is asserted.
                # The frontend never reaches them: `x % 0` raises before the
                # call and no generated program produces an infinity.
                if not math.isnan(mine_f):
                    bad.append((a, b, mine_f, "a NaN"))
                continue
            if math.isnan(want):
                if not math.isnan(mine_f):
                    bad.append((a, b, mine_f, "a NaN"))
            elif _bits(mine_f) != _bits(want):
                bad.append((a, b, mine_f, repr(want)))
        assert not bad, (
            f"{len(bad)} of {len(pairs)} disagree with libm; first: "
            + "; ".join(f"fmod({a!r}, {b!r}) gave {mine!r}, wanted {want}"
                        for a, b, mine, want in bad[:5]))


# The fuzzer's generated programs used to run here too, on twelve seeds. They
# now run in `test_differential.py` on twenty, alongside the other backends,
# which is where they belong: the generator knows nothing about AArch64, so a
# copy over here was the same test in a worse place -- and two places to
# update when the corpus changes.
