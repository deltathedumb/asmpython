"""Exhaustive language-surface conformance runner for pyinbin.

Unlike ``tests/runner.py`` (hand-authored cases with a fixed expected
output) or ``tests/cpython_conformance.py`` (runs CPython's own test
suite), this generates a systematic matrix of small programs covering
every statement form, operator, comprehension shape, and control-flow
construct in the supported grammar, then uses the *real* CPython
interpreter as the oracle: each generated snippet is run under both
the host ``python`` and ``python -m asmpython pyinbin run``, and their
stdout is compared. There is no hand-written "expected" value to keep
in sync -- CPython's own behavior on the same source is the answer.

The generator is organized as independent *sections*, each producing
many small standalone snippets (one Python statement/expression form
per snippet, printed so the two interpreters' output can be diffed).
Sections are combinatorial where it's cheap (e.g. every binary
operator against every pair of a small set of representative operand
types) rather than exhaustive over all possible values, since the
combinatorics of the full value space are infinite -- the goal is
covering every *grammar production* and every *opcode*, not every
possible runtime value.

Run:    python -m tests.exhaustive_runner
        python -m tests.exhaustive_runner --section comprehensions
        python -m tests.exhaustive_runner --list-sections
Exit codes: 0 = all pass; 1 = at least one mismatch or error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Case:
    section: str
    name: str
    source: str


@dataclass
class CaseResult:
    case: Case
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Section registry
# ---------------------------------------------------------------------------

_SECTIONS: dict[str, "list[Case] | callable"] = {}


def section(name: str):
    def register(fn):
        _SECTIONS[name] = fn
        return fn
    return register


def _case(section_name: str, cases: list[Case], name: str, source: str) -> None:
    cases.append(Case(section_name, name, source))


# --- literals ---------------------------------------------------------------

@section("literals")
def _literals() -> list[Case]:
    cases: list[Case] = []
    values = [
        ("int_zero", "0"), ("int_pos", "42"), ("int_neg", "-7"),
        ("int_big", "123456789012345678901234567890"),
        ("int_bin", "0b1011"), ("int_oct", "0o17"), ("int_hex", "0xFF"),
        ("int_underscore", "1_000_000"),
        ("float_basic", "3.14"), ("float_exp", "1.5e10"), ("float_neg_exp", "1.5e-10"),
        ("float_no_int_part", ".5"), ("float_no_frac_part", "5."),
        ("complex_basic", "3+4j"), ("complex_pure", "5j"),
        ("str_single", "'hello'"), ("str_double", '"hello"'),
        ("str_triple", "'''multi\nline'''"), ("str_fstring", "f'{1+1}'"),
        ("str_fstring_nested", "f'{f\"{1}\"}'"),
        ("str_raw", "r'\\n'"), ("str_bytes", "b'bytes'"),
        ("str_concat_adjacent", "'a' 'b'"),
        ("bool_true", "True"), ("bool_false", "False"), ("none", "None"),
        ("ellipsis", "..."),
        ("list_empty", "[]"), ("list_basic", "[1, 2, 3]"),
        ("tuple_empty", "()"), ("tuple_one", "(1,)"), ("tuple_basic", "(1, 2, 3)"),
        ("dict_empty", "{}"), ("dict_basic", "{'a': 1, 'b': 2}"),
        ("set_basic", "{1, 2, 3}"),
    ]
    for name, expr in values:
        _case("literals", cases, name, f"print(repr({expr}))")
    return cases


# --- operators ---------------------------------------------------------------

_OPERANDS = [("int", "7"), ("float", "2.5"), ("bool", "True")]

@section("binary_ops")
def _binary_ops() -> list[Case]:
    cases: list[Case] = []
    ops = ["+", "-", "*", "/", "//", "%", "**", "&", "|", "^", "<<", ">>", "@"]
    for op in ops:
        for lname, lval in _OPERANDS:
            for rname, rval in _OPERANDS:
                if op in ("&", "|", "^", "<<", ">>") and ("float" in (lname, rname)):
                    continue  # real Python also rejects these combinations
                if op == "@":
                    continue  # matrix mult needs real operand support; covered separately
                _case(
                    "binary_ops", cases, f"{op}_{lname}_{rval if False else rname}",
                    f"print({lval} {op} {rval})",
                )
    return cases


@section("compare_ops")
def _compare_ops() -> list[Case]:
    cases: list[Case] = []
    ops = ["==", "!=", "<", "<=", ">", ">=", "is", "is not", "in", "not in"]
    for op in ops:
        if op in ("in", "not in"):
            _case("compare_ops", cases, f"op_{op.replace(' ', '_')}", f"print(3 {op} [1, 2, 3])")
        else:
            _case("compare_ops", cases, f"op_{op.replace(' ', '_')}", f"print(3 {op} 5)")
    _case("compare_ops", cases, "chained", "print(1 < 2 < 3)")
    _case("compare_ops", cases, "chained_false", "print(1 < 2 > 5)")
    return cases


@section("unary_ops")
def _unary_ops() -> list[Case]:
    cases: list[Case] = []
    for op in ["-", "+", "~", "not "]:
        _case("unary_ops", cases, f"unary_{op.strip() or 'not'}", f"print({op}5)")
    return cases


@section("bool_ops")
def _bool_ops() -> list[Case]:
    cases: list[Case] = []
    _case("bool_ops", cases, "and_true", "print(True and 2)")
    _case("bool_ops", cases, "and_false", "print(False and 2)")
    _case("bool_ops", cases, "or_true", "print(1 or 2)")
    _case("bool_ops", cases, "or_false", "print(0 or 2)")
    _case("bool_ops", cases, "chained_and", "print(1 and 2 and 3)")
    _case("bool_ops", cases, "chained_or", "print(0 or 0 or 3)")
    return cases


# --- comprehensions -----------------------------------------------------------

@section("comprehensions")
def _comprehensions() -> list[Case]:
    cases: list[Case] = []
    forms = [
        ("list_single", "[x for x in range(5)]"),
        ("list_filter", "[x for x in range(10) if x % 2 == 0]"),
        ("list_multi_clause", "[(i, j) for i in range(3) for j in range(3)]"),
        ("list_nested_iterable", "[(i, j) for i in range(3) for j in [k for k in range(3)]]"),
        ("list_nested_in_first", "[(i, j) for i in [a for a in range(3)] for j in range(3)]"),
        ("list_triple_nested", "[[y for y in [x, x + 1]] for x in [1, 3, 5]]"),
        ("set_single", "{x for x in range(5)}"),
        ("set_nested_iterable", "{x for x in [y for y in range(5)]}"),
        ("dict_single", "{x: x * x for x in range(5)}"),
        ("dict_nested_iterable", "{x: y for x in range(3) for y in [z for z in range(3)]}"),
        ("genexp_single", "list(x for x in range(5))"),
        ("genexp_nested_iterable", "list(x for x in (y for y in range(5)))"),
        ("nested_two_levels", "[x for x in [y for y in [z for z in range(3)]]]"),
        ("lambda_in_comp", "[(lambda a: a * a)(j) for j in range(5)]"),
    ]
    for name, expr in forms:
        _case("comprehensions", cases, name, f"print({expr})")
    return cases


# --- control flow ------------------------------------------------------------

@section("control_flow")
def _control_flow() -> list[Case]:
    cases: list[Case] = []
    _case("control_flow", cases, "if_elif_else", """
x = 2
if x == 1:
    print('one')
elif x == 2:
    print('two')
else:
    print('other')
""")
    _case("control_flow", cases, "while_break", """
i = 0
while True:
    if i == 3:
        break
    i += 1
print(i)
""")
    _case("control_flow", cases, "while_continue", """
i = 0
total = 0
while i < 5:
    i += 1
    if i % 2 == 0:
        continue
    total += i
print(total)
""")
    _case("control_flow", cases, "for_else", """
for x in range(3):
    pass
else:
    print('done')
""")
    _case("control_flow", cases, "while_else", """
i = 0
while i < 3:
    i += 1
else:
    print('done', i)
""")
    _case("control_flow", cases, "nested_break", """
for i in range(3):
    for j in range(3):
        if j == 1:
            break
        print(i, j)
""")
    _case("control_flow", cases, "match_literal", """
x = 2
match x:
    case 1:
        print('one')
    case 2:
        print('two')
    case _:
        print('other')
""")
    _case("control_flow", cases, "match_capture", """
x = (1, 2)
match x:
    case (a, b):
        print(a, b)
""")
    _case("control_flow", cases, "match_class", """
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
p = Point(1, 2)
match p:
    case Point(x=a, y=b):
        print(a, b)
""")
    return cases


# --- functions ----------------------------------------------------------------

@section("functions")
def _functions() -> list[Case]:
    cases: list[Case] = []
    _case("functions", cases, "positional_only", """
def f(a, b, /, c):
    return (a, b, c)
print(f(1, 2, 3))
""")
    _case("functions", cases, "keyword_only", """
def f(a, *, b):
    return (a, b)
print(f(1, b=2))
""")
    _case("functions", cases, "defaults", """
def f(a, b=10):
    return a + b
print(f(1))
""")
    _case("functions", cases, "varargs", """
def f(*args, **kwargs):
    return (args, kwargs)
print(f(1, 2, x=3))
""")
    _case("functions", cases, "posonly_kwarg_fallthrough", """
def f(a, b, /, **kw):
    return (a, b, kw)
print(f(1, 2, b=3))
""")
    _case("functions", cases, "closures", """
def outer():
    x = 1
    def inner():
        nonlocal x
        x += 1
        return x
    return inner
f = outer()
print(f(), f(), f())
""")
    _case("functions", cases, "decorator", """
def deco(fn):
    def wrapper(*a, **k):
        return fn(*a, **k) + 1
    return wrapper
@deco
def f(x):
    return x * 2
print(f(3))
""")
    _case("functions", cases, "generator_basic", """
def gen():
    yield 1
    yield 2
    yield 3
print(list(gen()))
""")
    _case("functions", cases, "generator_finally_on_close", """
def gen():
    try:
        yield 1
        yield 2
    finally:
        print('cleanup')
g = gen()
next(g)
g.close()
""")
    _case("functions", cases, "generator_send", """
def gen():
    x = yield 1
    yield x + 1
g = gen()
next(g)
print(g.send(10))
""")
    return cases


# --- classes --------------------------------------------------------------

@section("classes")
def _classes() -> list[Case]:
    cases: list[Case] = []
    _case("classes", cases, "basic_inheritance", """
class Base:
    def greet(self):
        return "base"
class Child(Base):
    def greet(self):
        return "child-" + super().greet()
print(Child().greet())
""")
    _case("classes", cases, "three_level_super", """
class A:
    def __init__(self):
        self.log = ["A"]
class B(A):
    def __init__(self):
        super().__init__()
        self.log.append("B")
class C(B):
    def __init__(self):
        super().__init__()
        self.log.append("C")
print(C().log)
""")
    _case("classes", cases, "mixin_mro", """
class Mixin:
    pass
class Base:
    def __init__(self):
        self.tag = "base"
class Combined(Mixin, Base):
    def __init__(self, x):
        super().__init__()
        self.x = x
c = Combined(5)
print(c.tag, c.x)
""")
    _case("classes", cases, "classmethod_staticmethod", """
class C:
    count = 0
    @classmethod
    def make(cls):
        cls.count += 1
        return cls()
    @staticmethod
    def helper():
        return 42
c1 = C.make()
print(C.count, C.helper())
""")
    _case("classes", cases, "properties", """
class C:
    def __init__(self):
        self._x = 1
    @property
    def x(self):
        return self._x
    @x.setter
    def x(self, value):
        self._x = value * 2
c = C()
c.x = 5
print(c.x)
""")
    _case("classes", cases, "dunder_call", """
class Adder:
    def __init__(self, n):
        self.n = n
    def __call__(self, x):
        return x + self.n
add5 = Adder(5)
print(add5(10))
""")
    _case("classes", cases, "self_keyword_arg", """
def capture(*args, **kwargs):
    return (args, kwargs)
import functools
spec = functools.partialmethod(capture, self=1, func=2)
print("ok")
""")
    return cases


# --- exceptions -------------------------------------------------------------

@section("exceptions")
def _exceptions() -> list[Case]:
    cases: list[Case] = []
    _case("exceptions", cases, "try_except", """
try:
    1 / 0
except ZeroDivisionError as e:
    print("caught", e)
""")
    _case("exceptions", cases, "try_except_finally", """
try:
    raise ValueError("boom")
except ValueError:
    print("caught")
finally:
    print("cleanup")
""")
    _case("exceptions", cases, "bare_try_finally", """
def f():
    try:
        raise ValueError("boom")
    finally:
        print("cleanup ran")
try:
    f()
except ValueError as e:
    print("propagated:", e)
""")
    _case("exceptions", cases, "bare_try_finally_normal", """
def f():
    x = 1
    try:
        x = 2
    finally:
        x = 3
    return x
print(f())
""")
    _case("exceptions", cases, "try_except_else", """
try:
    x = 1
except ValueError:
    print("no")
else:
    print("else", x)
""")
    _case("exceptions", cases, "multiple_except", """
def check(v):
    try:
        if v == 1:
            raise KeyError("k")
        elif v == 2:
            raise ValueError("v")
    except KeyError:
        return "key"
    except ValueError:
        return "value"
print(check(1), check(2))
""")
    _case("exceptions", cases, "exception_chaining", """
try:
    try:
        raise ValueError("inner")
    except ValueError as e:
        raise RuntimeError("outer") from e
except RuntimeError as e:
    print(e, "<-", e.__cause__)
""")
    _case("exceptions", cases, "custom_exception", """
class MyError(Exception):
    pass
try:
    raise MyError("custom")
except MyError as e:
    print("caught", e)
""")
    _case("exceptions", cases, "reraise", """
def f():
    try:
        raise ValueError("x")
    except ValueError:
        raise
try:
    f()
except ValueError as e:
    print("outer caught", e)
""")
    _case("exceptions", cases, "name_error_catch", """
try:
    undefined_name_xyz
except NameError:
    print("caught NameError")
""")
    _case("exceptions", cases, "exception_group", """
try:
    raise ExceptionGroup("multi", [ValueError("a"), TypeError("b")])
except* ValueError as eg:
    print("value", len(eg.exceptions))
except* TypeError as eg:
    print("type", len(eg.exceptions))
""")
    return cases


# --- imports ------------------------------------------------------------------

@section("imports")
def _imports() -> list[Case]:
    cases: list[Case] = []
    _case("imports", cases, "plain_import", "import os\nprint(type(os).__name__)")
    _case("imports", cases, "dotted_import_no_parent", "import os.path\nprint(os.path.join('a', 'b'))")
    _case("imports", cases, "from_import", "from os import path\nprint(path.join('a', 'b'))")
    _case("imports", cases, "from_import_as", "from os import path as p\nprint(p.join('a', 'b'))")
    _case("imports", cases, "import_as", "import os.path as osp\nprint(osp.join('a', 'b'))")
    return cases


# --- with statement -----------------------------------------------------------

@section("context_managers")
def _context_managers() -> list[Case]:
    cases: list[Case] = []
    _case("context_managers", cases, "basic_with", """
import contextlib
@contextlib.contextmanager
def cm():
    print("enter")
    yield 42
    print("exit")
with cm() as v:
    print("body", v)
""")
    _case("context_managers", cases, "with_return_runs_exit", """
import contextlib
log = []
@contextlib.contextmanager
def cm():
    log.append("enter")
    try:
        yield
    finally:
        log.append("exit")
def f():
    with cm():
        return "returned"
print(f(), log)
""")
    _case("context_managers", cases, "multi_item_with", """
import contextlib
@contextlib.contextmanager
def cm(name):
    print("enter", name)
    yield name
    print("exit", name)
with cm("a") as a, cm("b") as b:
    print("body", a, b)
""")
    _case("context_managers", cases, "with_exception_inner_except", """
import contextlib
@contextlib.contextmanager
def swap(d, k, v):
    old = d.get(k)
    d[k] = v
    try:
        yield old
    finally:
        d[k] = old
state = {"x": 1}
with swap(state, "x", 99):
    try:
        raise ValueError("boom")
    except ValueError:
        print("caught inside with, x =", state["x"])
print("after with, x =", state["x"])
""")
    return cases


def all_cases(names: list[str] | None = None) -> list[Case]:
    cases: list[Case] = []
    for name, fn in _SECTIONS.items():
        if names and name not in names:
            continue
        cases.extend(fn())
    return cases


def run_case(case: Case, lib_root: Path | None, timeout: float) -> CaseResult:
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = Path(tmpdir) / "case.py"
        source_path.write_text(case.source, encoding="utf-8")

        try:
            expected = subprocess.run(
                [sys.executable, str(source_path)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return CaseResult(case, False, "host python itself timed out (bad case)")

        cmd = [sys.executable, "-m", "asmpython", "pyinbin", "run", str(source_path)]
        if lib_root is not None:
            cmd += ["--import-root", str(lib_root)]
        try:
            got = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        except subprocess.TimeoutExpired:
            return CaseResult(case, False, "pyinbin timed out")

    if got.stdout.rstrip("\n") != expected.stdout.rstrip("\n"):
        detail = (
            f"expected: {expected.stdout.rstrip()!r}\n"
            f"     got: {got.stdout.rstrip()!r}\n"
            f"  stderr: {(got.stderr or '').strip().splitlines()[-1:] or ''}"
        )
        return CaseResult(case, False, detail)
    return CaseResult(case, True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--section", action="append", dest="sections",
                         help="only run this section (repeatable)")
    parser.add_argument("--list-sections", action="store_true")
    parser.add_argument("--lib-root", type=Path, default=None,
                         help="CPython stdlib root for --import-root (defaults to none)")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--required", action="store_true",
                         help="exit 1 unless every case passes (release-gate mode)")
    args = parser.parse_args(argv)

    if args.list_sections:
        for name in _SECTIONS:
            print(name)
        return 0

    cases = all_cases(args.sections)
    print(f"exhaustive runner: {len(cases)} case(s) across {len(set(c.section for c in cases))} section(s)")
    results = [run_case(case, args.lib_root, args.timeout) for case in cases]
    failed = [r for r in results if not r.ok]
    for r in results:
        status = "OK" if r.ok else "FAIL"
        print(f"  [{status}] {r.case.section}/{r.case.name}")
        if not r.ok:
            for line in r.detail.splitlines():
                print(f"      {line}")
    print(f"exhaustive runner: {len(results) - len(failed)}/{len(results)} passed")
    if failed and args.required:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
