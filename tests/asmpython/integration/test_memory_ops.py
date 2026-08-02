"""Memory, indirect calls and switch, through every backend.

These opcodes are implemented in the C backend, in the x86-64 backend and in
the interpreter, and until now nothing ran them. The Python frontend has no
arrays, no pointers and no `match`, so it emits none of `alloca`, `load`,
`store`, `offset`, `global_addr`, `func_addr`, `call_ptr` or `switch` -- which
means eight of the thirty-nine opcodes were reachable only from hand-written
IR, and hand-written IR was tested for parsing and interpretation but never
compiled and run.

That is the gap where a backend can be confidently wrong. `store` writes a
value to an address, and the operand order is (value, address); getting it
backwards produces a program that writes somewhere plausible and reads back
whatever was there. The interpreter and both backends have to agree, and the
only way to know is to run all three.

Recursion and register pressure are here too, for the same reason: the
frontend supports them and no test used them.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from io import StringIO

import pytest

from asmpython import target as target_registry
from asmpython.backend import get as get_backend, load_builtin
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir import verify
from asmpython.ir.interpreter import Interpreter
from asmpython.ir.printer import parse_module, print_module

HAS_CC = shutil.which("gcc") or shutil.which("cc")
HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"

#: Frame storage: reserve, write, read back, and index with a byte offset.
STACK_IR = """\
module stack

export func main() -> i64 {
entry:
    %0 = ptr.alloca 32
    %1 = i64.const 111
    i64.store %1, %0
    %2 = i64.const 8
    %3 = ptr.offset %0, %2
    %4 = i64.const 222
    i64.store %4, %3
    %5 = i64.load %0
    %6 = i64.load %3
    %7 = i64.add %5, %6
    ret %7
}
"""

#: A read-only global, addressed and loaded a byte at a time.
GLOBAL_IR = """\
module globals

global table = "\\01\\02\\03\\04\\05\\06\\07\\08" readonly

export func main() -> i64 {
entry:
    %0 = ptr.global_addr @table
    %1 = i64.const 3
    %2 = ptr.offset %0, %1
    %3 = i8.load %2
    %4 = i64.extend %3
    ret %4
}
"""

#: An indirect call through a function address.
INDIRECT_IR = """\
module indirect

func triple(%0: i64) -> i64 {
entry:
    %1 = i64.const 3
    %2 = i64.mul %0, %1
    ret %2
}

export func main() -> i64 {
entry:
    %0 = ptr.func_addr @triple
    %1 = i64.const 14
    %2 = i64.call_ptr %0(%1)
    ret %2
}
"""

#: A multi-way branch. The frontend has no `match`, so nothing else emits it.
SWITCH_IR = """\
module switching

export func main() -> i64 {
entry:
    %0 = i64.const 2
    i64.switch %0, default other [0 -> zero] [1 -> one] [2 -> two]
zero:
    %1 = i64.const 100
    ret %1
one:
    %2 = i64.const 200
    ret %2
two:
    %3 = i64.const 42
    ret %3
other:
    %4 = i64.const 999
    ret %4
}
"""

#: Bit-level reinterpretation, which must not go through a numeric conversion.
BITCAST_IR = """\
module bits

export func main() -> i64 {
entry:
    %0 = f64.const 1.0
    %1 = i64.bitcast %0
    %2 = i64.const 4607182418800017408
    %3 = i64.eq %1, %2
    %4 = i64.extend %3
    ret %4
}
"""

CASES = {
    "stack": (STACK_IR, 333),
    "globals": (GLOBAL_IR, 4),
    "indirect": (INDIRECT_IR, 42),
    "switch": (SWITCH_IR, 42),
    "bitcast": (BITCAST_IR, 1),
}


def interpret(text: str) -> int:
    module = parse_module(text)
    verify(module)
    return Interpreter(module, out=StringIO()).run("main")


def build_and_run(text: str, backend: str, tmp_path) -> int:
    load_builtin()
    module = parse_module(text)
    verify(module)
    target = target_registry.get("c" if backend == "c" else HOST_TARGET)
    artifacts = get_backend(backend).emit(module, target)

    inputs = []
    for name, data in artifacts.items():
        p = tmp_path / name
        p.write_bytes(data)
        inputs.append(str(p))
    if backend != "c":
        from asmpython.link import write_runtime
        inputs.append(str(write_runtime(tmp_path)))
    exe = tmp_path / "out.exe"
    built = subprocess.run([HAS_CC, *inputs, "-o", str(exe)],
                           capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    return subprocess.run([str(exe)], capture_output=True).returncode


@pytest.mark.parametrize("name", sorted(CASES))
class TestEveryPathAgrees:
    def test_it_parses_and_round_trips(self, name):
        text, _ = CASES[name]
        module = parse_module(text)
        assert print_module(parse_module(print_module(module))) == \
            print_module(module)

    def test_the_interpreter_gets_the_expected_answer(self, name):
        text, expected = CASES[name]
        assert interpret(text) == expected

    @pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
    @pytest.mark.parametrize("backend", ["c", "x86-64"])
    def test_the_backends_agree_with_it(self, name, backend, tmp_path):
        text, expected = CASES[name]
        # NOT `expected & 0xFF`: Windows keeps the full 32-bit exit code while
        # POSIX truncates to a byte, so masking is right on one and wrong on
        # the other. Compare against whichever this platform will report.
        want = expected if sys.platform == "win32" else expected & 0xFF
        assert build_and_run(text, backend, tmp_path) == want


def narrow(name: str, body: str) -> str:
    return f"module {name}\n\nexport func main() -> i64 {{\nentry:\n{body}\n}}\n"


#: Arithmetic at widths the Python frontend never uses. It emits only i64,
#: f64 and i1, so every narrow type was implemented and unexercised -- and
#: x86-64 computed all of them at 64 bits and kept the answer. `i8.add 127, 1`
#: gave 128 where the IR says an i8 wraps to -128.
NARROW = {
    "i8_add_wraps": (narrow("a", """    %0 = i8.const 127
    %1 = i8.const 1
    %2 = i8.add %0, %1
    %3 = i64.extend %2
    ret %3"""), -128),
    "i8_trunc_sign_extends": (narrow("b", """    %0 = i64.const 200
    %1 = i8.trunc %0
    %2 = i64.extend %1
    ret %2"""), -56),
    "i16_add_wraps": (narrow("c", """    %0 = i16.const 32767
    %1 = i16.const 1
    %2 = i16.add %0, %1
    %3 = i64.extend %2
    ret %3"""), -32768),
    "u8_add_wraps_unsigned": (narrow("d", """    %0 = u8.const 255
    %1 = u8.const 1
    %2 = u8.add %0, %1
    %3 = i64.extend %2
    ret %3"""), 0),
    "i8_neg_of_min": (narrow("e", """    %0 = i8.const -128
    %1 = i8.neg %0
    %2 = i64.extend %1
    ret %2"""), -128),
    "i32_mul_wraps": (narrow("f", """    %0 = i32.const 100000
    %1 = i32.const 100000
    %2 = i32.mul %0, %1
    %3 = i64.extend %2
    ret %3"""), 1410065408),
    "u16_shift": (narrow("g", """    %0 = u16.const 65535
    %1 = u16.const 4
    %2 = u16.shl %0, %1
    %3 = i64.extend %2
    ret %3"""), 65520),
}


class TestNarrowIntegerWidths:
    """Every width the IR declares, computed at that width.

    `TRUNC` is the sharpest of these. Masking with `andq $0xFF` gives 200 for
    `i8.trunc 200`, and the answer is -56: a signed narrow type has to be
    sign-extended back, not merely masked.
    """

    @pytest.mark.parametrize("name", sorted(NARROW))
    def test_the_interpreter_is_the_reference(self, name):
        text, expected = NARROW[name]
        assert interpret(text) == expected

    @pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
    @pytest.mark.parametrize("name", sorted(NARROW))
    @pytest.mark.parametrize("backend", ["c", "x86-64"])
    def test_the_backends_agree(self, name, backend, tmp_path):
        text, expected = NARROW[name]
        # An exit code carries 32 bits on Windows and 8 on POSIX, so the
        # comparison is against the reference truncated the same way -- the
        # claim is agreement, not that a byte can hold -32768.
        mask = 0xFFFFFFFF if sys.platform == "win32" else 0xFF
        assert build_and_run(text, backend, tmp_path) == expected & mask


class TestStoreOperandOrder:
    """`store` takes (value, address). Backwards is silent."""

    def test_the_spec_says_value_first(self):
        from asmpython.ir.opcodes import Op, spec
        assert "value, add" in spec(Op.STORE).doc.replace("(", "")

    def test_storing_then_loading_returns_the_value_not_the_address(self):
        """With the operands swapped, a backend writes the address into the
        slot and reads it back -- a large plausible number instead of 111."""
        assert interpret(STACK_IR) == 333


PROGRAMS = {
    "recursion": """
        def fact(n: int) -> int:
            if n <= 1:
                return 1
            return n * fact(n - 1)

        def fib(n: int) -> int:
            if n < 2:
                return n
            return fib(n - 1) + fib(n - 2)

        def main() -> int:
            print(fact(10))
            print(fib(20))
            return 0
    """,
    "mutual_recursion": """
        def is_even(n: int) -> int:
            if n == 0:
                return 1
            return is_odd(n - 1)

        def is_odd(n: int) -> int:
            if n == 0:
                return 0
            return is_even(n - 1)

        def main() -> int:
            print(is_even(10))
            print(is_odd(7))
            return 0
    """,
}


def pressure_program(count: int) -> str:
    """Far more simultaneously-live values than there are registers.

    Spilling is where an allocator goes wrong, and it is the case a small
    program never reaches: with four live values every allocation is correct.
    """
    names = [f"v{i}" for i in range(count)]
    lines = ["def main() -> int:"]
    lines += [f"    {n}: int = {i * 7 + 1}" for i, n in enumerate(names)]
    lines.append("    total: int = 0")
    lines.append("    for k in range(3):")
    lines += [f"        total = total + {n} * k" for n in names]
    lines.append("    print(total)")
    lines.append("    print(" + " + ".join(names) + ")")
    lines.append("    return 0")
    return "\n".join(lines) + "\n"


def cpython_value(src: str) -> list[str]:
    out: list[str] = []
    ns = {"print": lambda *a: out.append(" ".join(str(x) for x in a))}
    exec(compile(src, "<t>", "exec"), ns)
    ns["main"]()
    return out


def run_source(src: str, tmp_path, backend: str | None) -> list[str]:
    import textwrap
    path = tmp_path / "p.py"
    path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    sink = DiagnosticSink()
    if backend is None:
        result = compile_source(Options(source=path, optimise=True), sink)
        assert result.ok, [d.message for d in sink.diagnostics]
        out = StringIO()
        Interpreter(result.module, out=out).run("main")
        return out.getvalue().split("\n")[:-1]
    result = compile_source(Options(
        source=path, output=tmp_path / "p.exe", backend=backend, link=True,
        optimise=True, workdir=tmp_path / "w",
        target=target_registry.get("c" if backend == "c" else HOST_TARGET)),
        sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    ran = subprocess.run([str(result.program)], capture_output=True, text=True)
    return ran.stdout.split("\n")[:-1]


class TestRecursionAndPressure:
    @pytest.mark.parametrize("name", sorted(PROGRAMS))
    def test_the_interpreter_matches_cpython(self, name, tmp_path):
        import textwrap
        src = textwrap.dedent(PROGRAMS[name]).strip() + "\n"
        assert run_source(src, tmp_path, None) == cpython_value(src)

    @pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
    @pytest.mark.parametrize("name", sorted(PROGRAMS))
    @pytest.mark.parametrize("backend", ["c", "x86-64"])
    def test_the_backends_match_cpython(self, name, backend, tmp_path):
        import textwrap
        src = textwrap.dedent(PROGRAMS[name]).strip() + "\n"
        assert run_source(src, tmp_path, backend) == cpython_value(src)

    @pytest.mark.parametrize("count", [8, 24, 40])
    def test_pressure_in_the_interpreter(self, count, tmp_path):
        src = pressure_program(count)
        assert run_source(src, tmp_path, None) == cpython_value(src)

    @pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
    @pytest.mark.parametrize("count", [24, 40])
    def test_pressure_compiled(self, count, tmp_path):
        """Forty live values against twelve allocatable registers."""
        src = pressure_program(count)
        assert run_source(src, tmp_path, "x86-64") == cpython_value(src)
