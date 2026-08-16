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

from tests import harness

from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter
from asmpython.ir.printer import parse_module, print_module

HAS_CC = shutil.which("gcc") or shutil.which("cc")

#: The host's object format decides which assembler directives are legal and
#: which ABI the system libraries expect. Guessing either produces output that
#: assembles on one platform and is silently wrong on the other.
_HOST_OBJECT_FORMAT = "coff" if sys.platform == "win32" else "elf"
_HOST_TARGET_NAME = ("x86_64-windows" if sys.platform == "win32"
                     else "x86_64-linux")

#: The runtime the frontend's `print` calls resolve against, plus an entry
#: point. Taken from the shipped runtime rather than written out again: a test
#: with its own copy stops testing the runtime and starts testing the copy, and
#: this one did -- it kept printing floats with `%f` after the real runtime
#: moved to Python's repr, so every float program "failed" against an oracle
#: that agreed with nothing.
def _runtime_c(entry: str) -> str:
    from asmpython.link.runtime import runtime_c
    return runtime_c(entry=entry)

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
    # Everything below was found untested by asking "which construct does
    # every existing program happen to avoid?" -- the question that turned up
    # `if`/`else`, whose entire else branch had been unreachable.
    "else_branches": """
        def classify(n: int) -> int:
            if n < 0:
                return 1
            elif n == 0:
                return 2
            elif n < 10:
                return 3
            else:
                return 4

        def main() -> int:
            print(classify(-1))
            print(classify(0))
            print(classify(5))
            print(classify(50))
            total: int = 0
            for i in range(6):
                if i % 2 == 0:
                    total = total + 1
                else:
                    total = total + 100
            print(total)
            return 0
    """,
    "and_or_yield_an_operand": """
        def main() -> int:
            a: int = 5
            b: int = 7
            print(a and b)
            print(0 and b)
            print(a or b)
            print(0 or b)
            return 0
    """,
    "void_functions": """
        def shout(n: int) -> None:
            print(n)

        def main() -> int:
            shout(3)
            shout(4)
            return 0
    """,
    "booleans": """
        def flip(b: bool) -> bool:
            return not b

        def main() -> int:
            t: bool = True
            print(int(flip(t)))
            print(int(t))
            print(int(t and False))
            print(int(1 < 2 < 3 < 4))
            print(int(1 < 2 < 1 < 4))
            return 0
    """,
    "nested_conditional_expressions": """
        def main() -> int:
            a: int = 3
            print(1 if a > 0 else (2 if a < -1 else 3))
            print(1 if a < 0 else (2 if a < -1 else 3))
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
            print(-a)
            print(2.0 ** 5)
            print(float(3) / 2.0)
            return 0
    """,
    "float_modulo": """
        def main() -> int:
            print(7.5 % 2.0)
            print(-7.5 % 2.0)
            print(7.5 % -2.0)
            print(-7.5 % -2.0)
            print(7.5 // 2.0)
            print(-7.5 // 2.0)
            return 0
    """,
    "float_comparison": """
        def main() -> int:
            a: float = 7.5
            b: float = 2.0
            if a > b:
                print(1)
            if b < a:
                print(2)
            if a == 7.5:
                print(3)
            if a != b:
                print(4)
            if a >= 7.5:
                print(5)
            if b <= 2.0:
                print(6)
            if not (a < b):
                print(7)
            return 0
    """,
    "float_calls": """
        def scale(x: float, k: float) -> float:
            return x * k

        def mixed(n: int, x: float) -> float:
            return float(n) + x

        def interleaved(a: int, b: float, c: int, d: float) -> float:
            return float(a) + b + float(c) + d

        def main() -> int:
            print(scale(3.5, 4.0))
            print(mixed(3, 0.25))
            print(interleaved(1, 2.5, 3, 4.25))
            return 0
    """,
}


def _render(value) -> str:
    """Format one printed value the way the runtime does.

    Which is now exactly the way CPython does -- so this is `str`, and the
    function is kept only so that the next divergence has an obvious place to
    be recorded. It used to special-case floats, because the runtime printed
    them with C's `%f` (`32.000000` for `32.0`); the runtime prints Python's
    repr now, and a test that still compensated would assert the old bug.
    """
    return str(value)


def cpython_output(src: str) -> tuple[list[str], int]:
    captured: list[str] = []
    namespace: dict = {
        "print": lambda *a: captured.append(" ".join(_render(x) for x in a))}
    exec(compile(src, "<test>", "exec"), namespace)
    # A program with top-level statements IS the program, and its exit code is
    # 0 -- the `def main() -> int:` convention is one shape, not the only one,
    # and every dynamic program below is written as an ordinary script. The
    # frontend makes the same choice: see `Analyzer._entry`.
    main = namespace.get("main")
    return captured, main() if callable(main) else 0


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


#: Programs on the DYNAMIC path: no annotations, every value a runtime object.
#: They are here rather than only in conformance/ because this file is the one
#: place all four paths are compared on the same source -- and until classes
#: existed the frontend could compile almost nothing that looked like ordinary
#: Python, so the dynamic path was end-to-end tested by nothing.
#:
#: Each one is chosen for a specific way the four could disagree, not for
#: coverage of syntax.
DYNAMIC_PROGRAMS = {
    # An instance is a new kind in the runtime, and the C, the interpreter's
    # handle table and the optimiser's view of an opaque pointer all have to
    # treat it the same way.
    "class_basics": """
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y
            def norm2(self):
                return self.x * self.x + self.y * self.y
            def __repr__(self):
                return "Point(" + repr(self.x) + ", " + repr(self.y) + ")"

        p = Point(3, 4)
        print(p.x, p.y)
        print(p.norm2())
        print(p)
        print(type(p).__name__)
        print(isinstance(p, Point))
    """,
    # `super()` resolves against the DEFINING class, which is baked in at
    # compile time. A backend that lost that constant would recurse forever
    # rather than print a wrong answer.
    "class_inheritance": """
        class Animal:
            kind = "animal"
            def __init__(self, name):
                self.name = name
            def speak(self):
                return "..."
            def describe(self):
                return self.name + " says " + self.speak()

        class Dog(Animal):
            def __init__(self, name, tricks):
                super().__init__(name)
                self.tricks = tricks
            def speak(self):
                return "woof"

        d = Dog("rex", 3)
        print(d.describe())
        print(d.kind, d.tricks)
        print(isinstance(d, Dog), isinstance(d, Animal))
    """,
    # Every dunder hook, including `[Vec(1)] == [Vec(1)]` -- which only works
    # if equality dispatches from INSIDE the container comparison rather than
    # at the top-level `==`.
    "class_dunders": """
        class Vec:
            def __init__(self, x):
                self.x = x
            def __add__(self, o):
                return Vec(self.x + o.x)
            def __radd__(self, o):
                return Vec(self.x + o)
            def __eq__(self, o):
                return self.x == o.x
            def __repr__(self):
                return "Vec(" + repr(self.x) + ")"
            def __len__(self):
                return self.x
            def __getitem__(self, i):
                return self.x * i
            def __bool__(self):
                return self.x != 0

        a = Vec(2)
        print(a + Vec(5), 10 + a)
        print(a == Vec(2), a == Vec(3))
        print(len(Vec(5)), Vec(5)[3])
        print(bool(Vec(0)), bool(Vec(1)))
        print([Vec(1)] == [Vec(1)])
    """,
    # THE closure test. Two closures over one variable must see each other's
    # writes, so what they capture is the BOX. A by-value capture passes every
    # other program here and fails this one.
    "closure_cell_is_shared": """
        def make():
            n = 0
            def bump():
                nonlocal n
                n = n + 1
                return n
            def read():
                return n
            return bump, read

        bump, read = make()
        print(read())
        bump()
        bump()
        print(read())

        def counter():
            total = 0
            def add(n):
                nonlocal total
                total = total + n
                return total
            return add

        c1 = counter()
        c2 = counter()
        print(c1(5), c1(3), c2(100))
    """,
    # A capture two levels down, which the middle function never mentions --
    # it still has to receive the box in order to pass it on.
    "closure_through_two_levels": """
        def outer(a):
            def mid():
                def inner():
                    return a * 2
                return inner()
            return mid()

        print(outer(21))

        def fact(n):
            if n <= 1:
                return 1
            return n * fact(n - 1)

        def apply(f, x):
            return f(x)

        print(apply(fact, 5))
    """,
    # A method name that a built-in container also defines. Which one
    # `x.add(1)` means is a runtime question, and the two answers are emitted
    # behind a test -- so both arms have to be right in all four paths.
    "class_method_name_collides_with_builtin": """
        class Bag:
            def __init__(self):
                self.items = []
            def add(self, x):
                self.items.append(x)
                return len(self.items)
            def __contains__(self, x):
                return x in self.items

        b = Bag()
        print(b.add(1), b.add(2))
        print(2 in b, 9 in b)
        s = set()
        s.add(7)
        print(sorted(s))
    """,
    # Defaults live in the function VALUE, because a method is always called
    # through a value and the caller never sees its signature.
    "class_default_arguments": """
        class Box:
            def __init__(self, v=0):
                self.v = v
            def add(self, n=1):
                self.v = self.v + n
                return self.v

        b = Box()
        print(b.v, b.add(), b.add(5))
        print(Box(7).v)
    """,
    # A three-level `super()` chain, which only terminates if each call starts
    # from the class the method was WRITTEN in rather than from type(self);
    # plus a method reached as a value, both bound and through the class.
    "class_super_chain_and_method_values": """
        class A:
            def f(self):
                return "A.f"

        class B(A):
            def f(self):
                return "B.f+" + super().f()

        class C(B):
            def f(self):
                return "C.f+" + super().f()

        print(C().f())
        m = C().f
        print(m())
        print(A.f(A()))

        class Counter:
            total = 0
            def __init__(self):
                Counter.total = Counter.total + 1

        Counter()
        Counter()
        print(Counter.total)
    """,
    # A user exception class is a NAME in the runtime's hierarchy rather than
    # a type object, so `except AppError:` catching a SubError goes through
    # the same walk that makes `except LookupError:` catch a KeyError.
    "user_exception_classes": """
        class AppError(Exception):
            pass

        class SubError(AppError):
            pass

        try:
            raise SubError("boom")
        except AppError as e:
            print(type(e).__name__, e)
            print(isinstance(e, Exception), isinstance(e, AppError))

        def move(v):
            try:
                raise SubError(v)
            except SubError as e:
                return e.args[0]

        print(move(7))
    """,
    # WHERE a default is evaluated. Both halves have to hold at once: the
    # module-level `def` runs once and shares one list, and the `def` in a loop
    # runs per iteration and captures that iteration's value.
    "default_argument_evaluation_point": """
        def acc(x, xs=[]):
            xs.append(x)
            return xs

        print(acc(1))
        print(acc(2))
        print(acc(3))

        fs = []
        for i in range(3):
            def g(n=i):
                return n * 10
            fs.append(g)
        print([f() for f in fs])

        def star(a, *rest):
            return a, rest

        h = star
        print(star(1), star(1, 2, 3), h(9, 8))
    """,
}

ALL_PROGRAMS = {**PROGRAMS, **FLOAT_PROGRAMS, **DYNAMIC_PROGRAMS}


@harness.cases("name", sorted(ALL_PROGRAMS))
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

    @harness.needs("cc")
    def test_x86_64_backend_matches_cpython(self, name, tmp_path):
        """Assemble the generated assembly and run it.

        This is the path that caught a real miscompilation: a loop counter
        passed as a call argument and read afterwards was placed in a
        caller-saved register, so the call destroyed it and the loop ended
        early. The interpreter was right, the backend was wrong, and only
        running both revealed which.
        """
        from asmpython.backend import get, load_builtin
        from asmpython.backends.x86_64.emit import UnsupportedOperation
        from asmpython.target import get as get_target
        load_builtin()
        src = self.program(name)
        want_out, want_value = cpython_output(src)
        module = compile_module(src, tmp_path, True)

        # The IR entry is `main`; C's main wraps it.
        module.function("main").name = "main_ir"
        target = get_target(_HOST_TARGET_NAME)
        try:
            asm = get("x86-64").emit(module, target)["out.s"]
        except UnsupportedOperation as exc:
            harness.skip(f"backend does not implement this yet: {exc}")

        s_file = tmp_path / "out.s"
        s_file.write_bytes(asm)
        rt = tmp_path / "rt.c"
        rt.write_text(_runtime_c("main_ir"), encoding="utf-8")
        exe = tmp_path / "out.exe"
        built = subprocess.run([HAS_CC, str(s_file), str(rt), "-o", str(exe)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        assert ran.stdout.split("\n")[:-1] == want_out
        assert ran.returncode == (want_value & 0xFF)

    @harness.needs("cc")
    def test_c_backend_matches_cpython(self, name, tmp_path):
        from asmpython.backend import get, load_builtin
        from asmpython.target import get as get_target
        load_builtin()
        src = self.program(name)
        want_out, want_value = cpython_output(src)
        module = compile_module(src, tmp_path, True)

        c_file = tmp_path / "out.c"
        c_file.write_bytes(get("c").emit(module, get_target("c"))["out.c"])
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
