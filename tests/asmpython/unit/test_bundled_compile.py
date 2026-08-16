"""`compile()` inside a produced binary answers what CPython's does.

THE PROBES ARE READ OUT OF THE CONFORMANCE CASES, not transcribed. Those cases
are the specification -- every `syntax/*` one is a list of sources and whether
`compile()` accepts each -- and copying the list here would let the two drift
apart silently, which is the one failure a test of this shape can have.

WHAT IS COMPARED is what a program can actually observe: accepted or not,
which exception CLASS (a program writes `except IndentationError:` and it must
fire), and which warnings were raised. Not the message text -- matching
CPython's wording is a second and much larger promise, and no case reads one.
"""
from __future__ import annotations

import ast
import pathlib
import sys
import warnings

from tests import harness

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_BUNDLED = (_ROOT / "src" / "asmpython" / "frontends" / "python" / "bundled")


def _load():
    """`_pycompile`, loaded with the bundled directory on the path only for
    as long as the import takes -- it holds a `typing.py`, an `io.py` and a
    dozen more names the standard library also has."""
    sys.path.insert(0, str(_BUNDLED))
    try:
        import _pycompile
        return _pycompile
    finally:
        if sys.path and sys.path[0] == str(_BUNDLED):
            del sys.path[0]


mine = _load()

#: The cases that call `compile()`. Every one is a specification of what it
#: must accept and refuse.
CASES = """async/await-outside-async-is-a-syntax-error
lexical/indentation-rules
pep/0414-explicit-unicode-literal/u-prefix-is-accepted
pep/0758-except-without-parens/unparenthesized-exception-group
pep/0765-control-flow-in-finally/return-in-finally-warns
pep/3110-exception-handling/except-as-and-scope
pep/3113-no-tuple-parameter-unpacking/rejected
pep/3127-integer-literal-syntax/octal-and-binary-forms
syntax/break-continue-outside-loop
syntax/decorator-and-class-forms
syntax/duplicate-and-reserved-names
syntax/f-string-and-literal-errors
syntax/invalid-assignment-targets
syntax/match-is-a-soft-keyword
syntax/nonlocal-and-global-rules
syntax/return-yield-outside-function
syntax/starred-and-parameter-rules
syntax/walrus-restrictions""".split()

#: The shapes a case writes out rather than listing in a tuple -- an
#: indentation probe, an `await` at module level, a `finally` that warns.
WRITTEN_OUT = [
    "def g():\nreturn 1",
    "if True:\n  a = 1\n   b = 2",
    "await 1",
    "async def f():\n    return await g()",
    "def f():\n    try:\n        pass\n    finally:\n        return 1",
    "def f():\n    for i in []:\n        try:\n            pass\n"
    "        finally:\n            break",
    "def f():\n    try:\n        pass\n    finally:\n        pass",
    "def f():\n    try:\n        pass\n    finally:\n        for i in []:\n"
    "            break",
]


def _probes() -> list[str]:
    """Every string literal the cases hand to `compile`, read off their
    trees -- so this cannot drift from what is measured."""
    out = []
    for name in CASES:
        tree = ast.parse((_ROOT / "conformance" / "cases"
                          / (name + ".py")).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Tuple, ast.List)) and node.elts and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str)
                    for e in node.elts):
                out.extend(e.value for e in node.elts)
    return out + WRITTEN_OUT


PROBES = _probes()


def _ask(fn, src: str) -> str:
    """What a program can see: the outcome, the exception class, the
    warnings."""
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn(src, "<probe>", "exec")
        marks = sorted({w.category.__name__ for w in caught})
        return ("accepted " + ",".join(marks)).strip()
    except IndentationError:
        return "IndentationError"        # BEFORE SyntaxError: it is a subclass
    except SyntaxError:
        return "SyntaxError"
    except ValueError:
        return "ValueError"


class TestAgreesWithCPython:
    @harness.cases("src", PROBES)
    def test_probe(self, src):
        assert _ask(mine.compile, src) == _ask(compile, src), repr(src)

    def test_there_are_probes(self):
        """A sweep over an empty list passes and proves nothing."""
        assert len(PROBES) > 70, len(PROBES)

    def test_answers_a_code_object(self):
        """`type(compile(...)).__name__` is `'code'`, which a case prints."""
        made = mine.compile("x = 1", "<t>", "exec")
        assert type(made).__name__ == "code"
        assert made.co_filename == "<t>"

    def test_refuses_an_unknown_mode(self):
        try:
            mine.compile("1", "<t>", "nonsense")
        except ValueError:
            return
        raise AssertionError("accepted a mode that does not exist")

    def test_eval_mode_refuses_a_statement(self):
        """The whole difference between `eval` and `exec`."""
        mine.compile("1 + 1", "<t>", "eval")
        try:
            mine.compile("x = 1", "<t>", "eval")
        except SyntaxError:
            return
        raise AssertionError("eval mode accepted a statement")

    def test_accepts_a_code_object_it_made(self):
        """`compile(compile(...))` is what a wrapper that normalises its
        argument does, and CPython hands the code object straight back."""
        made = mine.compile("x = 1", "<t>", "exec")
        assert mine.compile(made, "<t>", "exec") is made
