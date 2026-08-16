"""A suspension may appear anywhere an expression may, and nothing computed
before it may be held in a register.

THAT IS THE WHOLE OF IT. `await` and `yield` both compile to a RETURN out of
the step function, so a value the lowering is holding when one happens is in a
register no path writes on the way back in -- and the IR verifier refuses the
program, naming a block the source never had. Every shape below produced
exactly that, and every one is ordinary Python.

WHY A TEST AND NOT A CORPUS PROGRAM. The corpus runs each program through
three execution paths and compares its output with CPython, which is the right
check for BEHAVIOUR and costs a C compile. These ask a narrower question --
does the frontend produce IR the verifier accepts -- and asking it needs no
backend at all, so the whole sweep is a second. Four corpus programs cover the
behaviour; this covers the shapes, including the ones whose behaviour is
already covered elsewhere.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from tests import harness

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source

_ASYNC_HEAD = '''import asyncio


async def v(x):
    await asyncio.sleep(0)
    return x


async def ag(n):
    for i in range(n):
        await asyncio.sleep(0)
        yield i


def plain(a, b, c=0):
    return (a, b, c)


class Box:
    def __init__(self):
        self.items = []

    def take(self, a, b):
        self.items.append((a, b))
        return len(self.items)


async def main():
    xs = [10, 20, 30, 40]
    d = {"k": 5}
    b = Box()
    out = []
'''

_ASYNC_TAIL = "\n    return out\n\n\nprint(asyncio.run(main()))\n"

#: Every position an `await` can sit in. The name is what the shape is called
#: in a bug report; the value is one statement of a coroutine body.
AWAIT_SHAPES = {
    "two in one call": "out.append(plain(await v(1), await v(2)))",
    "in a keyword value": "out.append(plain(1, await v(2), c=await v(3)))",
    "in a method's arguments": 'out.append(b.take(await v("a"), await v("b")))',
    "as a list index": "out.append(xs[await v(1)])",
    "as a dict key": 'out.append(d[await v("k")])',
    "either side of an operator": "out.append(await v(3) + await v(4))",
    "in a list display": "out.append([await v(1), 2, await v(3)])",
    "in a dict display": 'out.append({"a": await v(1), "b": await v(2)})',
    "in a tuple display": "out.append((await v(1), await v(2)))",
    "in a set display": "out.append(sorted({await v(1), await v(2)}))",
    "either side of a comparison": "out.append(await v(1) < await v(2))",
    "in an f-string": "out.append(f\"{await v('x')}-{await v('y')}\")",
    "in an f-string's spec": 'out.append(f"{1:>{await v(3)}}")',
    "in a nested call": "out.append(plain(plain(await v(1), 2), await v(3)))",
    "as the receiver": "out.append((await v(b)).items)",
    "in a boolean operator": "out.append((await v(0)) or (await v(2)))",
    "in a conditional": "out.append(1 if await v(True) else await v(2))",
    "in an augmented assignment": "n = 1\n    n += await v(2)\n    out.append(n)",
    "as an assigned value": "xs[0] = await v(9)\n    out.append(xs[0])",
    "inside a spread": "out.append(plain(*[await v(1), await v(2)]))",
    "inside a double spread":
        "out.append(plain(**{'a': await v(1), 'b': await v(2)}))",
    "in a slice's bounds": "out.append(xs[await v(1):await v(3)])",
    "in a slice's step": "out.append(xs[await v(0):await v(4):await v(2)])",
    "in an assert message": "assert await v(True), await v('why')",
    "in a return": "return [await v(1)]",
    # An async comprehension SUSPENDS AND CONTAINS NO `await` -- the
    # suspension is the `async for` itself, which a walk looking for `Await`
    # nodes cannot see.
    "an async comprehension as an argument":
        "out.append([x async for x in ag(2)])",
    "an async comprehension in a call":
        "out.append(plain([x async for x in ag(2)], 1))",
    "an async comprehension in a display":
        'out.append({"k": [x async for x in ag(2)]})',
    "an async comprehension in a comparison":
        "out.append(len([x async for x in ag(2)]) < 9)",
}

_GEN_HEAD = '''def plain(a, b, c=0):
    return (a, b, c)


class Box:
    def __init__(self):
        self.items = []

    def take(self, a, b):
        self.items.append((a, b))
        return len(self.items)


def gen():
    xs = [10, 20, 30, 40]
    d = {"k": 5}
    b = Box()
    out = []
'''

_GEN_TAIL = "\n    return out\n\n\ng = gen()\nprint(g.send(None))\n"

#: THE SAME POSITIONS FOR `yield`, which suspends the same way. This half was
#: broken for longer and more completely: the check asked about `ast.Await`
#: alone, so a generator yielding mid-expression had no spill at all.
YIELD_SHAPES = {
    "two in one call": "out.append(plain((yield 1), (yield 2)))",
    "in a keyword value": "out.append(plain(1, (yield 2), c=(yield 3)))",
    "in a method's arguments": "out.append(b.take((yield 1), (yield 2)))",
    "as a list index": "out.append(xs[(yield 0)])",
    "either side of an operator": "out.append((yield 3) + (yield 4))",
    "in a list display": "out.append([(yield 1), 2, (yield 3)])",
    "in a dict display": 'out.append({"a": (yield 1), "b": (yield 2)})',
    "in a tuple display": "out.append(((yield 1), (yield 2)))",
    "in a set display": "out.append(sorted({(yield 1), (yield 2)}))",
    "either side of a comparison": "out.append((yield 1) < (yield 2))",
    "in an f-string": 'out.append(f"{(yield 1)}-{(yield 2)}")',
    "in an augmented assignment":
        "n = 1\n    n += (yield 2)\n    out.append(n)",
    "inside a spread": "out.append(plain(*[(yield 1), (yield 2)]))",
    "in a slice's bounds": "out.append(xs[(yield 0):(yield 3)])",
}


def _accepts(source: str):
    """None when the frontend produced IR the verifier accepts, else why."""
    path = Path(tempfile.mkdtemp()) / "prog.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path), sink)
    if result.ok:
        return None
    return "; ".join(d.message for d in sink.diagnostics) or "refused"


class TestASuspensionMayAppearAnywhere:
    @harness.cases("shape", sorted(AWAIT_SHAPES))
    def test_await(self, shape):
        why = _accepts(_ASYNC_HEAD + "    " + AWAIT_SHAPES[shape]
                       + _ASYNC_TAIL)
        assert why is None, f"await {shape}: {why}"

    @harness.cases("shape", sorted(YIELD_SHAPES))
    def test_yield(self, shape):
        why = _accepts(_GEN_HEAD + "    " + YIELD_SHAPES[shape] + _GEN_TAIL)
        assert why is None, f"yield {shape}: {why}"

    def test_the_probe_can_fail(self):
        """The sweep is only worth anything if a broken program is refused --
        otherwise every case above would pass against a compiler that accepted
        nothing but the empty file."""
        assert _accepts("def f(:\n    pass\n") is not None
