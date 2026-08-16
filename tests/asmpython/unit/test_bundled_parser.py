"""The bundled lexer and parser agree with CPython about what is ill-formed.

`compile()`, `exec()` and `eval()` exist in a produced binary to answer one
question -- is this source valid Python -- so AGREEMENT ABOUT ACCEPTANCE is
the whole contract. Not the error messages: a program catches `SyntaxError`
and reads the type, and matching CPython's wording would be a second and much
larger promise.

The sweep below is the real test. Every conformance case and every bundled
module is a few thousand lines of Python that CPython accepts, and a parser
that refuses one of them is broken in a way no hand-written probe would find.
The probes exist for the other direction: source CPython REFUSES, which the
corpus by definition contains none of.
"""
from __future__ import annotations

import ast
import pathlib
import sys

from tests import harness

_BUNDLED = (pathlib.Path(__file__).resolve().parents[3]
            / "src" / "asmpython" / "frontends" / "python" / "bundled")

# THE MODULES ARE LOADED AS THEMSELVES, not through the package: they are
# written to be spliced into a program, so they import each other by bare name
# and have no package to be part of.
#
# AND THE PATH IS PUT BACK. That directory holds a `dataclasses.py`, an
# `enum.py`, an `io.py` and a dozen more names the standard library also has,
# written for the subset a compiled program gets. Leaving it on `sys.path`
# shadows the real ones for the whole process -- and `multiprocessing` hands
# the parent's `sys.path` to every worker it spawns, so a runner that imports
# this file for COLLECTION poisons children that never run a test from it.
# What that looked like: every worker died at startup inside `import logging`,
# with `dataclass() got an unexpected keyword argument 'kw_only'`, and the
# whole suite failed at `-j 2` and passed at `-j 1`.
#
# Restoring is safe because both modules import eagerly and reach only their
# neighbours here; nothing resolves a bare name after this block.
_saved = list(sys.path)
sys.path.insert(0, str(_BUNDLED))
try:
    import _pylex      # noqa: E402
    import _pyparse    # noqa: E402
finally:
    sys.path[:] = _saved

_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _accepts(src: str, mode: str = "exec"):
    """None when the bundled parser accepts, else why it did not."""
    try:
        _pyparse.parse(src, mode)
        return None
    except _pylex.LexError as exc:
        return "lex: " + exc.msg
    except _pyparse.ParseError as exc:
        return "parse: " + exc.msg


def _cpython_accepts(src: str, mode: str = "exec") -> bool:
    try:
        ast.parse(src, mode=mode)
        return True
    except SyntaxError:
        return False


def _sources():
    out = sorted((_ROOT / "conformance" / "cases").rglob("*.py"))
    out += sorted(_BUNDLED.glob("*.py"))
    return out


#: Source the PARSER must refuse -- `ast.parse` refuses each of these too.
#: Every one is a shape a conformance case hands to `compile()`, or a rule
#: whose only job is to reject, so a parser that quietly accepted one would
#: turn a refusal into a wrong answer.
REFUSED = [
    "x = 1 +", "f() = 1", "1 = 2", "None = 1", "def f(*): pass",
    "@decorator\nx = 1", "try:\n    pass",
    "f'{}'", "f'{1+}'", "f'{x!z}'", "0x", "1_", "01", "ur'x'",
    "'unterminated", "x := 1", "import", "from x import", "global", "del",
    "except ValueError as (a, b): pass",
    "try:\n    pass\nexcept ValueError, TypeError as e:\n    pass",
    "if a:\n    b = 1\n  c = 2",
    "def f((a, b)): pass",
]

#: Source that PARSES and is still a `SyntaxError`, because CPython rejects
#: it in the COMPILER rather than the parser. `ast.parse("break")` builds a
#: tree quite happily; `compile("break", ...)` does not.
#:
#: Listed here, beside the parser they are NOT the responsibility of,
#: because this is where someone looks for them -- and because the parser
#: ACCEPTING them is a requirement rather than an oversight. `_pyvalidate`
#: is what has to reject them, and this list is its contract.
REFUSED_AFTER_PARSING = [
    "break", "continue", "return", "yield", "nonlocal x", "await x",
    "x = 1\nreturn x", "def f(a, a): pass", "x = *a",
]

#: Source CPython ACCEPTS that a parser is likely to get wrong. The corpus
#: covers ordinary code; these are the corners.
ACCEPTED = [
    "f'{x!r}'", "1_000", "0", "00", "0.5", "0x_FF", "u'x'", "rb'x'",
    'print(f"{d["k"]}")',
    "x = (yield)", "[x for x in y if z]", "{k: v for k, v in p}",
    "{*a, *b}", "{**a, 'k': 1}", "f(*a, **k)", "a[1:2, ::3]",
    "lambda *a, **k: 0", "lambda x: x", "def f(a, /, b, *, c): pass",
    "match x:\n    case 1 | 2:\n        pass",
    'match v:\n    case {"k": [a, b] as inner}:\n        pass',
    "match n:\n    case x if x < 0:\n        pass",
    "try:\n    pass\nexcept* ValueError:\n    pass",
    "try:\n    pass\nexcept ValueError, TypeError:\n    pass",
    "class C[T](Base, metaclass=M): pass", "type X[T] = list[T]",
    "x = -2 ** 2", "not a in b", "a is not b",
    "with (open(1) as f, open(2) as g): pass",
    "async def f():\n    async for x in y:\n        await z",
    "print(f'{x=}')", "def f() -> 'X': pass",
]


class TestAgreesWithCPython:
    @harness.cases("path", [p.as_posix() for p in _sources()])
    def test_every_real_source_parses(self, path):
        """Nothing CPython accepts may be refused.

        A refusal here would make `compile()` answer `SyntaxError` for a
        program that is fine -- which is worse than not implementing it, and
        is the failure a hand-written probe cannot find.
        """
        src = pathlib.Path(path).read_text(encoding="utf-8")
        if not _cpython_accepts(src):
            return                      # not our oracle for this file
        assert _accepts(src) is None, _accepts(src)

    @harness.cases("src", REFUSED)
    def test_refuses_what_cpython_refuses(self, src):
        """AND NOTHING CPython refuses may be accepted.

        This is the direction that turns a limitation into a wrong answer: a
        case expecting `SyntaxError` and getting `accepted` reads as one more
        ordinary failure.
        """
        assert not _cpython_accepts(src), "probe is no longer ill-formed"
        assert _accepts(src) is not None, "accepted: " + repr(src)

    @harness.cases("src", REFUSED_AFTER_PARSING)
    def test_parses_what_the_compiler_rejects(self, src):
        """A COMPILER ERROR IS NOT A PARSE ERROR, and the parser must not
        pretend otherwise.

        `break` outside a loop parses into a perfectly good tree. Refusing it
        here would put the check in the one place that cannot see whether a
        loop encloses it -- which is why `_pyvalidate` exists, and why this
        asserts the parser stays out of its way.
        """
        assert _cpython_accepts(src), "probe no longer parses in CPython"
        assert _accepts(src) is None, _accepts(src)

    @harness.cases("src", ACCEPTED)
    def test_accepts_the_corners(self, src):
        assert _cpython_accepts(src), "probe is no longer valid Python"
        assert _accepts(src) is None, _accepts(src)

    def test_eval_mode_refuses_a_statement(self):
        """`eval("x = 1")` is a SyntaxError, which is the whole difference
        between `eval` and `exec`."""
        assert _accepts("1 + 1", "eval") is None
        assert _accepts("x = 1", "eval") is not None
