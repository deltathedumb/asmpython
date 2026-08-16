"""Outcomes, and saying what went wrong.

THE POINT OF WRITING THIS RATHER THAN USING A GENERAL RUNNER. A failure here
is nearly always two lists of output lines that should have matched, and the
useful thing to print is WHERE THEY DIVERGED -- not that an assertion was
false. So a bare `assert a == b` is reconstructed from the traceback: the
frame is still alive, the operands are still in it, and the source line says
which names to read. That gives the diff without rewriting anyone's asserts.
"""
from __future__ import annotations

import ast
import linecache
import traceback
from dataclasses import dataclass
from enum import Enum


class Outcome(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    #: A guard this test declared failed, or something it depends on did. Not
    #: run at all, and counted apart from an ordinary skip so that a run which
    #: quietly stopped covering half the suite is visible.
    BLOCKED = "blocked"


@dataclass
class Result:
    id: str
    outcome: Outcome
    seconds: float = 0.0
    message: str = ""
    detail: str = ""


#: The expression forms safe to evaluate a second time. A CALL is not one:
#: `assert compiled(src) == expected(src)` would compile and run the program
#: again, and the second run's answer is not the one that failed -- it can
#: even agree, which is how this reported "lengths differ: 13 vs 13" about two
#: lists that were equal.
_PURE = (ast.Name, ast.Attribute, ast.Subscript, ast.Constant, ast.Tuple,
         ast.List, ast.Slice, ast.UnaryOp, ast.Starred)


def _is_pure(node) -> bool:
    """Can this be evaluated again without doing anything?

    Conservative on purpose: a diff is a convenience, and there is no version
    of it worth running the test's side effects twice for. When the answer is
    no the traceback is printed instead, which is never wrong -- only less
    helpful.
    """
    return all(isinstance(sub, _PURE) or isinstance(sub, ast.expr_context)
               for sub in ast.walk(node))


def _operands(exc: BaseException) -> str:
    """The two sides of a failed `assert a == b`, diffed.

    Read out of the FRAME the assertion failed in, which the traceback still
    holds: the names are whatever the source line says, and their values are
    still bound.

    ONLY SIDE-EFFECT-FREE OPERANDS are read -- see `_is_pure`. Evaluating a
    call here would run the test's work a second time, and the second answer
    is not the one that failed.
    """
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    if tb is None:
        return ""
    frame = tb.tb_frame
    line = linecache.getline(frame.f_code.co_filename, tb.tb_lineno).strip()
    if not line.startswith("assert"):
        return ""
    try:
        node = ast.parse(line, mode="exec").body[0]
    except SyntaxError:
        return ""
    test = getattr(node, "test", None)
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return ""
    if not isinstance(test.ops[0], (ast.Eq, ast.NotEq)):
        return ""

    if not (_is_pure(test.left) and _is_pure(test.comparators[0])):
        return ""

    def value_of(sub):
        try:
            return eval(compile(ast.Expression(sub), "<assert>", "eval"),
                        dict(frame.f_globals), dict(frame.f_locals))
        except Exception:
            return _UNREADABLE

    left, right = value_of(test.left), value_of(test.comparators[0])
    if left is _UNREADABLE or right is _UNREADABLE:
        return ""
    return _diff(left, right)


class _Unreadable:
    pass


_UNREADABLE = _Unreadable()


def _diff(left, right) -> str:
    """Where two values first differ, with a little context.

    Whole-value dumps are what makes a failing corpus test unreadable: the
    programs print dozens of lines and one of them is wrong. The first
    difference IS the finding.
    """
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        out = []
        for i in range(max(len(left), len(right))):
            a = left[i] if i < len(left) else "<missing>"
            b = right[i] if i < len(right) else "<missing>"
            if a != b:
                out.append(f"  first difference at index {i}:")
                out.append(f"    got:  {a!r}")
                out.append(f"    want: {b!r}")
                break
        if not out:
            out.append(f"  lengths differ: {len(left)} vs {len(right)}")
        if len(left) != len(right):
            out.append(f"  ({len(left)} items vs {len(right)})")
        return "\n".join(out)
    if isinstance(left, str) and isinstance(right, str) \
            and ("\n" in left or "\n" in right):
        return _diff(left.splitlines(), right.splitlines())
    return f"  got:  {left!r}\n  want: {right!r}"


def describe(exc: BaseException) -> tuple[str, str]:
    """A one-line message and the detail beneath it."""
    frames = traceback.extract_tb(exc.__traceback__)
    # The test's OWN frame, not the harness's: the deepest one under `tests/`
    # is where the reader wants to look.
    where = ""
    for frame in reversed(frames):
        if "harness" not in frame.filename:
            where = f"{frame.filename}:{frame.lineno} in {frame.name}"
            break
    head = str(exc) or type(exc).__name__
    if isinstance(exc, AssertionError):
        detail = _operands(exc)
        source = frames[-1].line if frames else ""
        body = (f"  {source}\n{detail}" if detail else
                "".join(traceback.format_exception(exc))[-2000:])
        return head, f"{where}\n{body}"
    return f"{type(exc).__name__}: {head}", (
        where + "\n" + "".join(traceback.format_exception(exc))[-2000:])


@dataclass
class Report:
    """What a run produced, and whether it was a good one."""

    results: list[Result]

    def count(self, outcome: Outcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome)

    @property
    def ok(self) -> bool:
        return self.count(Outcome.FAIL) == 0

    def slowest(self, n: int = 10) -> list[Result]:
        return sorted(self.results, key=lambda r: -r.seconds)[:n]

    def summary(self) -> str:
        passed = self.count(Outcome.PASS)
        failed = self.count(Outcome.FAIL)
        skipped = self.count(Outcome.SKIP)
        blocked = self.count(Outcome.BLOCKED)
        parts = [f"{passed} passed"]
        if failed:
            parts.append(f"{failed} FAILED")
        if skipped:
            parts.append(f"{skipped} skipped")
        if blocked:
            parts.append(f"{blocked} blocked")
        return ", ".join(parts)
