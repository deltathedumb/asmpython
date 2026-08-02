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

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter
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


class TestTheDocumentedDiagnostics:
    """Every code the frontend emits is in docs/LANGUAGE.md, and vice versa.

    A table of error codes is exactly the kind of documentation that rots: a
    new diagnostic is added where the check is, and nobody remembers the list
    three directories away. Checked in both directions -- an undocumented code
    is a gap, and a documented code that no longer exists is a lie.
    """

    def codes_in_source(self) -> set[str]:
        import re
        from pathlib import Path
        import asmpython.frontends.python.analysis as analysis
        text = Path(analysis.__file__).read_text(encoding="utf-8")
        return set(re.findall(r'"(E\d{4})"', text))

    def codes_in_docs(self) -> set[str]:
        import re
        from pathlib import Path
        docs = Path(__file__).resolve().parents[3] / "docs" / "LANGUAGE.md"
        return set(re.findall(r"E\d{4}", docs.read_text(encoding="utf-8")))

    def test_every_emitted_code_is_documented(self):
        missing = self.codes_in_source() - self.codes_in_docs()
        assert not missing, f"undocumented diagnostics: {sorted(missing)}"

    def test_every_documented_code_exists(self):
        stale = self.codes_in_docs() - self.codes_in_source()
        assert not stale, f"documented but not emitted: {sorted(stale)}"


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


class TestElse:
    """`if`/`else`, which was silently broken for the whole life of the tree.

    `Block` defines `__len__`, so a freshly created (empty) one is FALSY, and
    the lowerer chose the false edge with `else_b or join`. It picked the join
    every time: every else body in the language was unreachable, and the code
    in it simply did not run.

    Nothing caught it because every test program and every example was written
    in the early-return style -- `if n < 0: return -1` -- with no else
    anywhere. The fuzzer found it within minutes of learning to emit one.
    """

    def test_the_else_branch_runs(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                n: int = -5
                if n > 0:
                    print(1)
                else:
                    print(2)
                return 0
        """, tmp_path)
        assert lines == ["2"]

    def test_the_then_branch_still_runs(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                n: int = 5
                if n > 0:
                    print(1)
                else:
                    print(2)
                return 0
        """, tmp_path)
        assert lines == ["1"]

    def test_elif_chains(self, tmp_path):
        lines, _ = run_text("""
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
                return 0
        """, tmp_path)
        assert lines == ["1", "2", "3", "4"]

    def test_else_inside_a_loop(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                total: int = 0
                for i in range(6):
                    if i % 2 == 0:
                        total = total + 1
                    else:
                        total = total + 100
                print(total)
                return 0
        """, tmp_path)
        assert lines == ["303"]

    def test_the_false_edge_targets_the_else_block(self, tmp_path):
        """Asserted on the IR, because the wrong edge is invisible in output
        whenever the else body happens to be empty or unobservable."""
        from asmpython.ir.opcodes import Op
        result, _ = compile_text("""
            def main() -> int:
                n: int = 1
                if n > 0:
                    n = 2
                else:
                    n = 3
                return n
        """, tmp_path)
        fn = result.module.function("main")
        branch = next(i for b in fn.blocks for i in b.instructions
                      if i.op is Op.BRANCH)
        assert branch.labels[1].startswith("else"), (
            f"false edge goes to {branch.labels[1]!r}, not the else block")


class TestTheEntryPoint:
    """`main`'s signature is fixed, and saying so is the frontend's job.

    A `main` that takes an argument compiles to IR the backend emits happily
    and then fails at the C compiler, complaining about a wrapper function the
    user never wrote. That is a confusing place to learn the entry point has a
    required shape.
    """

    def test_parameters_are_refused(self, tmp_path):
        _, sink = compile_text("""
            def main(x: int) -> int:
                return x
        """, tmp_path)
        assert "E0008" in codes(sink)

    @pytest.mark.parametrize("returns", ["None", "float", "bool"])
    def test_the_return_type_is_int(self, returns, tmp_path):
        _, sink = compile_text(f"""
            def main() -> {returns}:
                return {"" if returns == "None" else "0"}
        """, tmp_path)
        assert "E0009" in codes(sink), codes(sink)

    def test_a_correct_main_is_accepted(self, tmp_path):
        result, sink = compile_text("""
            def main() -> int:
                return 0
        """, tmp_path)
        assert result.ok, [d.message for d in sink.diagnostics]

    def test_other_functions_may_return_anything(self, tmp_path):
        result, sink = compile_text("""
            def nothing() -> None:
                print(1)

            def fraction() -> float:
                return 0.5

            def main() -> int:
                nothing()
                print(fraction())
                return 0
        """, tmp_path)
        assert result.ok, [d.message for d in sink.diagnostics]


class TestUnreachableCode:
    """Python allows dead code after a terminator; so must this.

    A terminated block cannot take another instruction -- the builder refuses,
    correctly. Lowering on regardless raised `RuntimeError: block already ends
    in 'ret'` at the user, for a program CPython runs without complaint.
    """

    def test_after_return(self, tmp_path):
        lines, _ = run_text("""
            def f(n: int) -> int:
                if n > 0:
                    return 1
                    print(99)
                return 0

            def main() -> int:
                print(f(1))
                print(f(-1))
                return 0
        """, tmp_path)
        assert lines == ["1", "0"]

    def test_after_break_and_continue(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                total: int = 0
                for i in range(5):
                    if i == 2:
                        break
                        print(99)
                    total = total + i
                    continue
                    print(98)
                print(total)
                return 0
        """, tmp_path)
        assert lines == ["1"]

    def test_the_dead_statements_are_not_emitted(self, tmp_path):
        """Dropped, not merely unexecuted: they must not reach the IR, where
        they would be instructions in a block that already returned."""
        result, _ = compile_text("""
            def main() -> int:
                return 1
                print(12345)
        """, tmp_path)
        text = str(result.module.function("main"))
        assert "12345" not in text

    def test_a_function_whose_every_path_returns(self, tmp_path):
        lines, _ = run_text("""
            def f(n: int) -> int:
                if n > 0:
                    return 1
                else:
                    return 2

            def main() -> int:
                print(f(1))
                print(f(-1))
                return 0
        """, tmp_path)
        assert lines == ["1", "2"]


class TestAugmentedAssignment:
    """`x += 1` is `x = x + 1`, and must be checked as one.

    Analysis used to look only at the right-hand side, so none of the operator
    rules applied: `x **= n` type-checked as ordinary arithmetic and then hit
    the lowering table that requires a literal exponent, raising at the user.
    Analysis and lowering now share one synthetic node, so they cannot look at
    different trees.
    """

    def test_every_arithmetic_operator(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                x: int = 20
                x += 5
                print(x)
                x -= 3
                print(x)
                x *= 2
                print(x)
                x //= 7
                print(x)
                x %= 4
                print(x)
                x **= 3
                print(x)
                return 0
        """, tmp_path)
        assert lines == ["25", "22", "44", "6", "2", "8"]

    def test_every_bitwise_operator(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                x: int = 12
                x &= 10
                print(x)
                x |= 5
                print(x)
                x ^= 3
                print(x)
                x <<= 2
                print(x)
                x >>= 1
                print(x)
                return 0
        """, tmp_path)
        assert lines == ["8", "13", "14", "56", "28"]

    def test_floats(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                f: float = 7.5
                f /= 2.0
                print(f)
                f *= 4.0
                print(f)
                return 0
        """, tmp_path)
        assert lines == ["3.750000", "15.000000"]

    @pytest.mark.parametrize("statement, code", [
        ("x **= n", "E0043"),          # runtime exponent
        ("x **= -1", "E0044"),         # negative exponent
        ("x @= 2", "E0045"),           # unsupported operator
        ("x /= 2", "E0060"),           # float result into an int
    ])
    def test_the_operator_rules_apply(self, statement, code, tmp_path):
        _, sink = compile_text(f"""
            def main() -> int:
                n: int = 2
                x: int = 3
                {statement}
                return 0
        """, tmp_path)
        assert code in codes(sink), codes(sink)

    def test_bitwise_on_a_float_is_refused(self, tmp_path):
        _, sink = compile_text("""
            def main() -> int:
                f: float = 1.5
                f &= 2
                return 0
        """, tmp_path)
        assert "E0042" in codes(sink)

    def test_it_reads_the_target_first(self, tmp_path):
        """`x += 1` reads x, so an unassigned x is an error."""
        _, sink = compile_text("""
            def main() -> int:
                c: int = 0
                if c > 0:
                    y: int = 1
                y += 1
                return 0
        """, tmp_path)
        assert "E0032" in codes(sink)


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
        from asmpython.ir.opcodes import Op
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
