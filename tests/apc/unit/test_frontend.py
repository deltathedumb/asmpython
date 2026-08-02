"""The Python frontend: what it accepts, what it refuses, and how it refuses.

The central test here is `TestNothingCrashes`. Analysis and lowering are two
passes over the same tree with a contract between them -- lowering may assume
analysis accepted everything it sees -- and the failure mode when that contract
breaks is not a wrong answer but a traceback with a compiler stack in it.

That is exactly what `**` did: analysis typed it like any other binary
operator, lowering looked it up in a table of five, and the user got a KeyError.
Every individual pass was tested; the pair was not. So this file feeds a corpus
of Python at the whole pipeline and asserts only that the compiler behaves like
a compiler -- a result or a diagnostic, never an exception.
"""
from __future__ import annotations

import textwrap

import pytest

from apc.diagnostics import DiagnosticSink
from apc.driver import Options, compile_source
from apc.ir.interpreter import Interpreter
from io import StringIO


def compile_text(src: str, tmp_path, *, optimise: bool = False):
    path = tmp_path / "prog.py"
    path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path, optimise=optimise), sink)
    return result, sink


def codes(sink) -> list[str]:
    return [d.code for d in sink.diagnostics]


def run_text(src: str, tmp_path) -> tuple[list[str], int]:
    result, sink = compile_text(src, tmp_path)
    assert result.ok, [d.message for d in sink.diagnostics]
    out = StringIO()
    value = Interpreter(result.module, out=out).run("main")
    lines = out.getvalue().split("\n")[:-1] if out.getvalue() else []
    return lines, value


#: Python that this subset does NOT accept. Each must produce a diagnostic --
#: the point is not which one, but that the compiler reports rather than dies.
UNSUPPORTED = [
    "class C: pass",
    "import os",
    "from os import path",
    "x = [1, 2, 3]",
    "x = {1: 2}",
    "x = (1, 2)",
    "x = {1, 2}",
    "x = 'hello'",
    "x = f'{1}'",
    "x = [i for i in range(3)]",
    "x = lambda: 1",
    "del x",
    "global x",
    "with open('f') as f: pass",
    "try:\n    pass\nexcept Exception:\n    pass",
    "assert 1 == 1",
    "raise ValueError()",
    "yield 1",
    "x: int = 1\nx += y = 2" if False else "x = y = 2",
    "async def f(): pass",
    "def f(*args) -> int: return 0",
    "def f(**kw) -> int: return 0",
    "def f(a=1) -> int: return 0",
    "@dec\ndef f() -> int: return 0",
    "x = a.b",
    "x = a[0]",
    "x = a()",
    "x = 1 if 2 else 'three'",
    "x = -'a'",
    "x = 1 @ 2",
    "match 1:\n    case 1: pass",
    "x = (y := 2)",
    "for i in [1, 2]: pass",
    "for i, j in range(3): pass",
    "while True: pass\nelse: pass",
    "break",
    "continue",
    "x = 2 ** n",
    "x = 2 ** -1",
    "x = int(1, 2)",
    "nonlocal x",
    "def f() -> int:\n    def g() -> int: return 1\n    return g()",
]


class TestNothingCrashes:
    """A result or a diagnostic. Never a traceback."""

    @pytest.mark.parametrize("snippet", UNSUPPORTED)
    def test_unsupported_python_is_reported_not_raised(self, snippet, tmp_path):
        src = f"def main() -> int:\n" + textwrap.indent(
            textwrap.dedent(snippet), "    ") + "\n    return 0\n"
        path = tmp_path / "prog.py"
        path.write_text(src, encoding="utf-8")
        sink = DiagnosticSink()
        try:
            result = compile_source(Options(source=path), sink)
        except Exception as exc:                      # noqa: BLE001
            pytest.fail(f"compiler raised {type(exc).__name__}: {exc}\n"
                        f"on:\n{src}")
        assert not result.ok or sink.diagnostics, (
            f"accepted silently and produced no diagnostic:\n{src}")
        if not result.ok:
            assert all(d.code for d in sink.diagnostics), \
                "every diagnostic needs a code"

    @pytest.mark.parametrize("snippet", UNSUPPORTED)
    def test_at_module_level_too(self, snippet, tmp_path):
        path = tmp_path / "prog.py"
        path.write_text(textwrap.dedent(snippet).strip() + "\n"
                        "def main() -> int:\n    return 0\n", encoding="utf-8")
        sink = DiagnosticSink()
        try:
            compile_source(Options(source=path), sink)
        except Exception as exc:                      # noqa: BLE001
            pytest.fail(f"compiler raised {type(exc).__name__}: {exc}")


class TestPower:
    def test_literal_exponent_is_expanded(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                print(2 ** 10)
                print(5 ** 0)
                print(7 ** 1)
                print((-2) ** 3)
                return 0
        """, tmp_path)
        assert lines == ["1024", "1", "7", "-8"]

    def test_expansion_uses_squaring_not_repeated_multiplication(self, tmp_path):
        """`x ** 8` is three multiplications, not eight."""
        result, _ = compile_text("""
            def main() -> int:
                n: int = 3
                return n ** 8
        """, tmp_path)
        from apc.ir.opcodes import Op
        muls = sum(1 for f in result.module.defined_functions()
                   for b in f.blocks for i in b.instructions if i.op is Op.MUL)
        assert muls == 3, f"expected 3 multiplications for **8, got {muls}"

    def test_runtime_exponent_is_refused_with_a_reason(self, tmp_path):
        _, sink = compile_text("""
            def main() -> int:
                n: int = 2
                return 2 ** n
        """, tmp_path)
        assert "E0043" in codes(sink)

    def test_negative_exponent_suggests_the_float_form(self, tmp_path):
        _, sink = compile_text("""
            def main() -> int:
                return 2 ** -1
        """, tmp_path)
        assert "E0044" in codes(sink)
        assert any("1.0 /" in h for d in sink.diagnostics for h in d.helps)

    def test_float_base_gives_a_float(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                print(2.0 ** 5)
                return 0
        """, tmp_path)
        assert lines == ["32.000000"]


class TestBreakContinue:
    def test_break_leaves_the_loop(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                total: int = 0
                for i in range(10):
                    if i == 5:
                        break
                    total = total + i
                print(total)
                return 0
        """, tmp_path)
        assert lines == ["10"]

    def test_continue_reaches_the_increment(self, tmp_path):
        """The bug this guards: `continue` jumping to the test instead of the
        step leaves the counter unchanged and the loop never terminates."""
        lines, _ = run_text("""
            def main() -> int:
                odd: int = 0
                for j in range(10):
                    if j % 2 == 0:
                        continue
                    odd = odd + j
                print(odd)
                return 0
        """, tmp_path)
        assert lines == ["25"]

    def test_break_applies_to_the_innermost_loop(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                count: int = 0
                for a in range(4):
                    for b in range(4):
                        if b == 2:
                            break
                        count = count + 1
                print(count)
                return 0
        """, tmp_path)
        assert lines == ["8"]

    def test_break_in_a_while(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                n: int = 0
                while True:
                    n = n + 1
                    if n > 3:
                        break
                print(n)
                return 0
        """, tmp_path)
        assert lines == ["4"]

    def test_outside_a_loop_is_an_error(self, tmp_path):
        """ast.parse accepts this; Python's own compiler does not."""
        _, sink = compile_text("""
            def main() -> int:
                break
                return 0
        """, tmp_path)
        assert "E0027" in codes(sink)


class TestRange:
    def test_descending(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                total: int = 0
                for k in range(5, 0, -1):
                    total = total + k
                print(total)
                return 0
        """, tmp_path)
        assert lines == ["15"]

    def test_a_negative_literal_step_is_recognised(self, tmp_path):
        """`-1` is UnaryOp(USub, 1), not Constant(-1). Treating it as
        "not a literal" made this loop run zero times and report success."""
        lines, _ = run_text("""
            def main() -> int:
                n: int = 0
                for k in range(3, 0, -1):
                    n = n + 1
                print(n)
                return 0
        """, tmp_path)
        assert lines == ["3"]

    def test_stepped_ascending(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                total: int = 0
                for m in range(0, 10, 3):
                    total = total + m
                print(total)
                return 0
        """, tmp_path)
        assert lines == ["18"]

    def test_runtime_step_is_refused(self, tmp_path):
        """The sign of the step chooses the loop test. Unknown sign, no loop."""
        _, sink = compile_text("""
            def main() -> int:
                s: int = -1
                for k in range(5, 0, s):
                    pass
                return 0
        """, tmp_path)
        assert "E0028" in codes(sink)

    def test_zero_step_is_refused(self, tmp_path):
        _, sink = compile_text("""
            def main() -> int:
                for k in range(0, 5, 0):
                    pass
                return 0
        """, tmp_path)
        assert "E0029" in codes(sink)


class TestConversions:
    def test_int_truncates_toward_zero(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                print(int(2.7))
                print(int(-2.7))
                return 0
        """, tmp_path)
        assert lines == ["2", "-2"]

    def test_bool_is_a_comparison_not_a_truncation(self, tmp_path):
        """`bool(2)` truncated to one bit is 0. It must be `x != 0`."""
        lines, _ = run_text("""
            def main() -> int:
                print(int(bool(2)))
                print(int(bool(0)))
                print(int(bool(2.5)))
                print(int(bool(4)))
                return 0
        """, tmp_path)
        assert lines == ["1", "0", "1", "1"]

    def test_float_widens(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                print(float(3))
                return 0
        """, tmp_path)
        assert lines == ["3.000000"]

    def test_wrong_arity_is_reported(self, tmp_path):
        _, sink = compile_text("""
            def main() -> int:
                return int(1, 2)
        """, tmp_path)
        assert "E0054" in codes(sink)

    def test_a_user_function_may_shadow_a_conversion(self, tmp_path):
        """`int` is not a keyword. If the program defines one, it wins."""
        lines, _ = run_text("""
            def int(x: float) -> int:
                return 99

            def main() -> int:
                print(int(1.5))
                return 0
        """, tmp_path)
        assert lines == ["99"]


class TestSpans:
    def test_instructions_carry_their_own_source_position(self, tmp_path):
        """Every instruction used to inherit whichever statement was lowered
        last, so a backend error pointed at the wrong line."""
        result, _ = compile_text("""
            def main() -> int:
                a: int = 1
                b: int = 2
                c: int = 3
                return a + b + c
        """, tmp_path)
        fn = result.module.function("main")
        lines = {i.span.start for b in fn.blocks for i in b.instructions
                 if i.span is not None and i.span.end > i.span.start}
        assert len(lines) > 1, \
            "all instructions share one span -- spans are not being recorded"
