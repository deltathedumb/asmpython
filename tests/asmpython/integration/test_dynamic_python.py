"""Ordinary Python, run three ways and compared against CPython.

`test_endtoend.py` does this for the statically typed subset. This does it for
the DYNAMIC path -- the one a Python script actually takes, where every value
is a runtime object and every operation a call into `objects/csource.py`.

Each program is checked against CPython through:

    1. the reference interpreter, on the IR the frontend produced
    2. the C backend, compiled and executed

Two paths rather than the four `test_endtoend` uses, because these programs
are large enough that a per-program x86-64 assemble-and-link would dominate the
suite's runtime, and the machine backends are covered by the differential
fuzzer instead.

WHY THESE PROGRAMS. Each was written while landing the feature it covers and
each one caught something. They are kept as a corpus rather than folded into
one file because a failure then names the feature: if `exceptions` fails and
`sequences` passes, the handler chain is what broke.

`tools/dynamic_diff.py` generates programs from the same grammar and is the
better bug-finder -- it found a falsy-empty-`Block` that made every `else`
branch unreachable. This is the regression half: what is known to work, kept
working, named.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from tests import harness

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter

HAS_CC = shutil.which("gcc") or shutil.which("cc")

PROGRAMS = {
    "traceback_positions": """
        try:
            (1).missing
        except AttributeError as e:
            tb = e.__traceback__
            print(tb is not None)
            print(tb.tb_lineno)
            code = tb.tb_frame.f_code
            print(hasattr(code, "co_positions"))
            rows = list(code.co_positions())
            print(len(rows) > 0, all(len(p) == 4 for p in rows))
            # Every row is a real span: it starts no later than it ends. A row of
            # Nones is CPython's for a synthetic instruction, and counts as one.
            print(all(p[0] is None or p[0] <= p[1] for p in rows))
            # And the line the traceback reports is one of them.
            print(tb.tb_lineno in [p[0] for p in rows])

        try:
            d = {}
            d["missing"]
        except KeyError as e:
            print(e.__traceback__.tb_lineno)

        try:
            n = 1 / 0
        except ZeroDivisionError as e:
            print(e.__traceback__.tb_lineno)

        try:
            raise ValueError("written out")
        except ValueError as e:
            print(e.__traceback__.tb_lineno)

        # An exception that was never raised has no traceback.
        print(ValueError("unraised").__traceback__)
    """,
    "buffers_grow_and_are_released": """
        # THE BLOCK ALLOCATOR, exercised through the things that use it. A
        # list's item array is the one allocation this runtime genuinely frees
        # -- it DOUBLES on growth and a slice assignment hands the old one
        # back -- so `runtime/blocks.py` puts size classes and free lists over
        # the arena, and every buffer below is handed out, grown, released and
        # handed out again.
        #
        # WHAT A BROKEN ALLOCATOR LOOKS LIKE HERE: not a crash, but an element
        # read out of a block that was reused while still live. So every case
        # checks CONTENTS after the reuse rather than only lengths.
        xs = []
        for i in range(200):
            xs.append(i)
        print(len(xs), xs[0], xs[199], sum(xs))

        # A release, then a rebuild that should take the same blocks back.
        xs[0:150] = []
        print(len(xs), xs[0], xs[-1])
        for i in range(300):
            xs.append(i * 2)
        print(len(xs), xs[49], xs[50], xs[-1])

        # Several live at once, so the free lists cannot hand the same block
        # to two of them.
        a = list(range(100))
        b = list(range(100, 200))
        c = list(range(200, 300))
        a[0:50] = []
        print(len(a), len(b), len(c), a[0], b[0], c[0], a[-1], b[-1], c[-1])
        for i in range(200):
            a.append(-i)
        print(len(a), a[0], a[49], a[50], a[-1], b[0], b[99], c[0], c[99])

        # Dicts grow the same way, two buffers at a time.
        d = {}
        for i in range(150):
            d[i] = i * i
        print(len(d), d[0], d[149], sorted(d)[:3])
        for i in range(75):
            del d[i]
        print(len(d), d[75], d[149], min(d), max(d))

        # Sets, and a nesting so the blocks are interleaved rather than
        # allocated and freed in order.
        s = set()
        for i in range(200):
            s.add(i % 91)
        print(len(s), min(s), max(s))
        rows = [list(range(k, k + 30)) for k in range(40)]
        for row in rows:
            row[0:10] = []
        print(len(rows), len(rows[0]), rows[0][0], rows[39][0], rows[39][-1])

        # Tuples share the sequence buffer and are built once.
        t = tuple(range(120))
        print(len(t), t[0], t[119], t[60])

        # An empty list, a one-element list and a list that never grows are
        # the three sizes the smallest class has to get right.
        print(len([]), len([1]), [1][0], len([1, 2]), [1, 2][1])
    """,
    "extending_a_builtin": """
        # `class D(dict)` and its four siblings, which `collections` is built
        # on. An instance carries a real dict/list/tuple/set and DELEGATES to
        # it for everything the class body does not define -- attributes,
        # iteration, `in`, `len`, the operators, `repr`, `hash` and the
        # constructor. Every one of those was a separate entry point and each
        # is here, because getting one wrong is a silently wrong answer rather
        # than an error: the object still claims `isinstance(d, dict)`.
        class D(dict):
            def __missing__(self, k):
                return "missing:" + str(k)


        d = D()
        d["a"] = 1
        print(d, d["a"], len(d), sorted(d.keys()), d["zz"])
        print(sorted(d.items()), d.get("a"), d.get("q", 7))
        print(isinstance(d, dict), d == {"a": 1}, dict(d))
        print([k for k in d], sorted(k for k in d), list(d))
        print("a" in d, "zz" in d)
        d.update({"b": 2})
        print(sorted(d.items()), d.pop("b"), sorted(d.items()))


        class Init(dict):
            def __init__(self, *a, **k):
                super().__init__(*a)
                self.tag = "t"


        i = Init({"x": 1})
        print(i, i.tag, len(i))


        class L(list):
            def second(self):
                return self[1]


        l = L([3, 1, 2])
        print(l, l.second(), len(l), isinstance(l, list))
        l.append(4)
        l.sort()
        print(l, l.index(3), l.count(1), l[1:], list(reversed(l)))


        class T(tuple):
            def __new__(cls, *args):
                return super().__new__(cls, list(args))


        t = T(1, 2, 3)
        print(t, len(t), t[1], t + (4,), t == (1, 2, 3))
        a, b, c = t
        print(a, b, c, len({T(1, 2), T(1, 2)}), hash(t) == hash((1, 2, 3)))


        class S(set):
            pass


        s = S([1, 2])
        s.add(3)
        print(sorted(s), 2 in s, len(s))


        # THE CLASS BODY WINS over the builtin, which is what lets a `Counter`
        # define `update` next to `dict.update`.
        class Own(dict):
            def keys(self):
                return "mine"

            def __repr__(self):
                return "Own!"


        o = Own()
        o["k"] = 1
        print(o.keys(), repr(o), len(o), o["k"])


        # A plain instance is untouched by any of it.
        class Plain:
            def __getattr__(self, name):
                return "fallback:" + name


        print(Plain().whatever, Plain().append)
    """,
    "spread_calls_onto_every_callable": """
        class B:
            def m(self, a, b):
                return (a, b)


        class C(B):
            def m(self, *args):
                return super().m(*args)


        def plain(a, b, c=0):
            return (a, b, c)


        xs = [1, 2]
        print(C().m(*xs))
        print(plain(*xs))
        print(plain(*xs, c=3))
        print(plain(**{"a": 1, "b": 2}))
        print(B().m(*xs))
        # `max(*xs)` IS `max(xs)` -- both ask for the largest of these.
        print(max(*xs), min(*xs))
        print(max(*[[1], [2, 3]]))
        print(sorted(*[[3, 1, 2]]))
        # `str.format` is chosen by NAME at the call site, so the spread has to
        # reach it the same way an ordinary call does.
        print("{}-{}".format(*xs))
        print("{a}!".format(**{"a": 5}))
        print("{}{}{c}".format(*xs, c="!"))
        print(*xs, sep="/")

        d = {"a": 1}
        print(sorted(dict(**d, b=2).items()))
        print(sorted(dict(**d, **{"c": 3}).items()))
        print(sorted(dict(x=1, y=2).items()))
        print(sorted(dict(d).items()), dict())
    """,
    "await_in_slices_specs_and_asserts": """
        import asyncio


        async def v(x):
            await asyncio.sleep(0)
            return x


        async def ag(n):
            for i in range(n):
                await asyncio.sleep(0)
                yield i


        def plain(a, b):
            return (a, b)


        async def main():
            out = []
            xs = [10, 20, 30, 40]
            # A slice whose bounds suspend.
            out.append(xs[await v(1):await v(3)])
            out.append(xs[await v(0):await v(4):await v(2)])
            # A format spec that suspends.
            out.append(f"{7:>{await v(4)}}")
            # An assert whose message suspends.
            try:
                assert await v(False), await v("why")
            except AssertionError as e:
                out.append(str(e))
            # An async comprehension held across a call.
            out.append(plain([x async for x in ag(2)], 1))
            out.append({"k": [x async for x in ag(2)]})
            out.append(len([x async for x in ag(3)]) < 9)
            # `**` with awaits in the values.
            out.append(plain(**{"a": await v(1), "b": await v(2)}))
            return out


        for one in asyncio.run(main()):
            print(one)
    """,
    "generators_and_coroutines_mixed": """
        import asyncio


        async def v(x):
            await asyncio.sleep(0)
            return x


        async def agen(n):
            # An async generator: `yield` and `await` in one frame, and both of them
            # inside expressions.
            total = 0
            for i in range(n):
                total += await v(i)
                yield (await v(i), total)


        async def main():
            out = []
            async for pair in agen(3):
                out.append(pair)
            # An async comprehension, which is a frame of its own.
            out.append([x async for x in agen(2)])
            out.append([await v(i) for i in range(3)])
            return out


        def plain_gen():
            got = []
            got.append((yield "a"))
            got.append((yield "b"))
            return got


        g = plain_gen()
        g.send(None)
        g.send(1)
        try:
            g.send(2)
        except StopIteration as stop:
            print(stop.value)
        for one in asyncio.run(main()):
            print(one)
    """,
    "yield_in_every_expression_position": """
        def plain(a, b, c=0):
            return (a, b, c)


        class Box:
            def __init__(self):
                self.items = []

            def take(self, a, b):
                self.items.append((a, b))
                return len(self.items)


        def gen():
            # `yield` SUSPENDS exactly as `await` does, so every expression position
            # that holds a value across one has the same problem -- and every one of
            # these used to produce invalid IR.
            xs = [10, 20, 30]
            d = {"k": 5}
            b = Box()
            out = []
            out.append(plain((yield 1), (yield 2)))
            out.append(plain(1, (yield 3), c=(yield 4)))
            out.append(b.take((yield 5), (yield 6)))
            out.append(b.items)
            out.append(xs[(yield 0) % 3])
            out.append(d["k" if (yield "k") else "k"])
            out.append((yield 7) + (yield 8))
            out.append([(yield 9), 2, (yield 10)])
            out.append({"a": (yield 11), "b": (yield 12)})
            out.append(((yield 13), (yield 14)))
            out.append((yield 15) < (yield 16))
            out.append(f"{(yield 17)}-{(yield 18)}")
            n = 1
            n += (yield 19)
            out.append(n)
            out.append(plain(*[(yield 20), (yield 21)]))
            out.append(sorted({(yield 22), (yield 23)}))
            return out


        g = gen()
        sent = 0
        try:
            got = g.send(None)
            while True:
                sent += 1
                # WHAT IS SENT BACK is what the expression evaluates to, so the values
                # below are the sent ones and not the yielded ones.
                got = g.send(sent)
        except StopIteration as stop:
            for one in stop.value:
                print(one)
        print("yields", sent)
    """,
    "await_in_every_expression_position": """
        import asyncio


        async def v(x):
            await asyncio.sleep(0)
            return x


        def plain(a, b, c=0):
            return (a, b, c)


        class Box:
            def __init__(self):
                self.items = []

            def take(self, a, b):
                self.items.append((a, b))
                return len(self.items)


        async def main():
            out = []
            # A call with an await among the arguments.
            out.append(plain(await v(1), await v(2)))
            out.append(plain(1, await v(2), c=await v(3)))
            # A method call on an object, with awaits.
            b = Box()
            out.append(b.take(await v("a"), await v("b")))
            out.append(b.items)
            # A subscript indexed by an await.
            xs = [10, 20, 30]
            out.append(xs[await v(1)])
            d = {"k": 5}
            out.append(d[await v("k")])
            # Binary operators either side of a suspension.
            out.append(await v(3) + await v(4))
            out.append([await v(1), 2, await v(3)])
            out.append({"a": await v(1), "b": await v(2)})
            out.append((await v(1), await v(2)))
            # A comparison and a boolean operator.
            out.append(await v(1) < await v(2))
            # An f-string with awaits in it.
            out.append(f"{await v('x')}-{await v('y')}")
            # A nested call.
            out.append(plain(plain(await v(1), 2), await v(3)))
            return out


        for one in asyncio.run(main()):
            print(one)
    """,
    "asyncio_tasks_and_groups": """
        import asyncio

        log = []


        async def slow():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                log.append("cancelled")
                raise


        async def val(v):
            await asyncio.sleep(0)
            return v


        async def late():
            await asyncio.sleep(5)
            return "never"


        async def main():
            task = asyncio.create_task(slow())
            await asyncio.sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                log.append("awaited-cancel")

            # A task that finishes normally: the result is readable after.
            t = asyncio.create_task(val(7))
            log.append(await t)
            log.append((t.done(), t.result(), t.cancelled()))

            # `wait_for` gives up at its deadline.
            try:
                await asyncio.wait_for(late(), timeout=0.01)
            except asyncio.TimeoutError:
                log.append("timeout")
            # And answers normally when the coroutine is quick enough.
            log.append(await asyncio.wait_for(val(3), timeout=1))

            # A task group does not finish while its children are running.
            async with asyncio.TaskGroup() as tg:
                made = [tg.create_task(val(n)) for n in (1, 2, 3)]
            log.append([t.result() for t in made])
            return log


        print(asyncio.run(main()))
        print(asyncio.TimeoutError is TimeoutError)
    """,
    "async_iterator_on_a_class": """
        import asyncio


        class Counting:
            def __init__(self, n):
                self.n = n
                self.i = 0

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self.i >= self.n:
                    raise StopAsyncIteration
                # A REAL SUSPENSION between items, which is what makes this more than
                # a synchronous loop wearing an `async` hat.
                await asyncio.sleep(0)
                self.i += 1
                return self.i


        class Pairs:
            # `__aiter__` may answer something OTHER than self.
            def __init__(self, items):
                self.items = items

            def __aiter__(self):
                return Counting(len(self.items))


        async def main():
            out = []
            async for v in Counting(3):
                out.append(v)
            async for v in Counting(0):
                out.append("never")
            async for v in Pairs(["a", "b"]):
                out.append(("pair", v))
            # An async generator still works, and is its own iterator.
            async def gen():
                for i in range(2):
                    await asyncio.sleep(0)
                    yield i * 10
            async for v in gen():
                out.append(v)
            return out


        print(asyncio.run(main()))
    """,
    "class_body_runs_as_a_block": """
        class C:
            values = [1, 2, 3]
            # The OUTERMOST iterable is evaluated in the class scope; everything else
            # in the comprehension is not, which is what the NameError below is.
            doubled = [v * 2 for v in values]
            try:
                bad = [v * len(values) for v in range(2)]
            except NameError:
                bad = "NameError"

            if len(values) > 2:
                flag = "big"
            else:
                flag = "small"

            total = 0
            for n in values:
                total = total + n

            def scaled(self):
                # A METHOD reads the class attribute through `self`, which is the
                # ordinary way and unaffected by any of the above.
                return [x * self.total for x in self.values]

            label = flag + ":" + str(total)


        print(C.doubled, C.bad)
        print(C.flag, C.total, C.label)
        print(C.n)
        print(C().scaled())

        # A `try`/`except ImportError` around an import, which is how a class picks
        # an optional dependency.
        class D:
            try:
                import math
                have = True
            except ImportError:
                have = False

            name = "D" if have else "?"


        print(D.have, D.name)

        # A while loop, and a name bound only in one branch.
        class E:
            i = 0
            while i < 3:
                i = i + 1
            if i == 3:
                done = True
            parts = []
            for w in ("a", "b"):
                parts.append(w * i)


        print(E.i, E.done, E.parts)
    """,
    "exception_class_with_a_body": """
        class AppError(Exception):
            # An exception class with a body, which is how most are written.
            kind = "app"

            def __init__(self, code, message):
                super().__init__(f"{code}: {message}")
                self.code = code

            def summary(self):
                return f"{self.kind}/{self.code}"


        class NotFound(AppError):
            def __init__(self, what):
                super().__init__(404, what)
                self.what = what


        try:
            raise AppError(500, "boom")
        except AppError as e:
            print(e.code, str(e), e.args)
            print(e.summary(), e.kind)
            print(type(e).__name__)

        # A subclass reaches its base's `__init__` through `super()`.
        try:
            raise NotFound("page")
        except AppError as e:
            print(e.code, e.what, str(e))
            print(type(e).__name__, isinstance(e, AppError))

        # It is still caught by its BASE, which is what the hierarchy is for.
        try:
            raise NotFound("again")
        except Exception as e:
            print("base caught", e.code)

        # An exception class with an EMPTY body still works, and is not a class with
        # a body in disguise.
        class Plain(ValueError):
            pass


        try:
            raise Plain("p")
        except ValueError as e:
            print(type(e).__name__, str(e), e.args)

        # Built without raising, then raised later.
        held = AppError(1, "held")
        print(held.code, str(held))
        try:
            raise held
        except AppError as e:
            print("reraised", e.code)

        # The class is a value: `issubclass` and `__name__` both answer.
        print(AppError.__name__, issubclass(NotFound, AppError))

        # A default `__init__` that never calls super still reads back what it was
        # passed, because `args` is set before it runs.
        class Quiet(RuntimeError):
            def __init__(self, tag):
                self.tag = tag


        try:
            raise Quiet("t")
        except RuntimeError as e:
            print(e.tag, e.args)
    """,
    "template_strings_keep_the_pieces": """
        name = "world"
        n = 5
        w = 8

        t = t"hello {name}!"
        print(type(t).__name__)
        print(t.strings)
        print([i.expression for i in t.interpolations])
        print([i.value for i in t.interpolations])
        print(t.values)

        # The conversion and the spec are RECORDED, not applied.
        t2 = t"{n!r:>{w}}"
        i = t2.interpolations[0]
        print(i.expression, i.conversion, repr(i.format_spec), i.value)
        print(t2.strings)

        # Adjacent fields leave an empty piece between them, so `strings` stays one
        # longer than `interpolations`.
        t3 = t"{n}{name}"
        print(t3.strings, len(t3.interpolations))

        # No fields at all.
        t4 = t"plain"
        print(t4.strings, t4.interpolations, t4.values)

        # Two templates are the same type.
        print(type(t) is type(t4))

        # Joining is the CONSUMER's job, which is the whole point.
        print(t.strings[0] + str(t.values[0]) + t.strings[1])

        # The expression source survives, which an f-string throws away.
        total = t"{n + w}"
        print(total.interpolations[0].expression, total.values[0])
    """,
    "except_star_divides": """
        log = []

        # A BARE exception is a group of one to `except*`.
        try:
            raise ValueError("v")
        except* ValueError as eg:
            log.append(("bare", type(eg).__name__, len(eg.exceptions)))

        # Nothing matches: the ORIGINAL propagates, not a wrapper.
        try:
            try:
                raise KeyError("k")
            except* ValueError as eg:
                log.append(("wrong", 1))
        except KeyError as e:
            log.append(("through", type(e).__name__))

        # Part matches, part is left over.
        try:
            try:
                raise ExceptionGroup("g", [ValueError("a"), KeyError("b")])
            except* ValueError as eg:
                log.append(("half", len(eg.exceptions)))
        except ExceptionGroup as e:
            log.append(("left", len(e.exceptions), type(e.exceptions[0]).__name__))

        # A tuple of types in one clause.
        try:
            raise ExceptionGroup("g", [ValueError("a"), KeyError("b"), TypeError("c")])
        except* (ValueError, KeyError) as eg:
            log.append(("tuple", len(eg.exceptions)))
        except* TypeError as eg:
            log.append(("rest", len(eg.exceptions)))

        # `finally` runs on the way out, matched or not.
        try:
            try:
                raise ExceptionGroup("g", [OSError("o")])
            except* ValueError as eg:
                log.append(("no", 1))
            finally:
                log.append(("finally", 1))
        except ExceptionGroup:
            log.append(("escaped", 1))

        # No exception at all: the clauses are skipped and `else` runs.
        try:
            log.append(("body", 1))
        except* ValueError as eg:
            log.append(("never", 1))
        else:
            log.append(("else", 1))
        finally:
            log.append(("fin2", 1))

        for item in log:
            print(item)
    """,
    "with_statement": """
        class Tracer:
            def __init__(self, name, swallow):
                self.name = name
                self.swallow = swallow
            def __enter__(self):
                print('enter', self.name)
                return self.name
            def __exit__(self, et, ev, tb):
                print('exit', self.name, et.__name__ if et else None)
                return self.swallow

        with Tracer('plain', False) as who:
            print('body', who)
        with Tracer('outer', True):
            with Tracer('inner', False):
                raise ValueError('boom')
        print('after')
        def early():
            with Tracer('returning', False):
                return 'value'
        print(early())
        with Tracer('a', False), Tracer('b', False):
            print('both')
    """,
    "parameter_kinds": """
        # `/` and `*` in a signature: which arguments a position can reach,
        # and which only a name can.
        def pos(a, b, /, c, d=4):
            return (a, b, c, d)
        p = pos
        print(p(1, 2, 3), p(1, 2, c=3), p(1, 2, 3, 4))
        try:
            p(1, b=2, c=3)
        except TypeError as e:
            print('TypeError', e)
        def kwo(a, *, b, c=3):
            return (a, b, c)
        k = kwo
        print(k(1, b=2), k(1, b=2, c=9))
        try:
            k(1, 2)
        except TypeError as e:
            print('TypeError', e)
        def both(a, /, b, *, c):
            return (a, b, c)
        bo = both
        print(bo(1, 2, c=3), bo(1, b=2, c=3))
        # A positional-only name is COLLECTED by `**kw` rather than matching.
        def collect(a, /, **kw):
            return (a, sorted(kw.items()))
        co = collect
        print(co(1, a=9, z=1))
        class Box:
            def __call__(self, /, *args, **kwargs):
                return (args, sorted(kwargs.items()))
        print(Box()(1, 2, x=3))
    """,
    "definite_assignment_joins": """
        # A handler that cannot fall through does not dilute the join: the
        # only path reaching the use DID assign.
        def wrap(n):
            try:
                result = 10 // n
            except ZeroDivisionError as exc:
                raise ValueError('bad') from exc
            return result
        print(wrap(2))
        def scan(items):
            out = []
            for item in items:
                try:
                    value = int(item)
                except ValueError:
                    continue
                out.append(value)
            return out
        print(scan(['1', 'x', '3']))
        def both(n):
            try:
                v = n * 2
            except ValueError:
                v = 0
            return v
        print(both(3))
        def fin():
            try:
                a = 1
            finally:
                b = 2
            return a + b
        print(fin())
        def orelse(n):
            try:
                x = n
            except ValueError:
                return 'no'
            else:
                y = x + 1
            return y
        print(orelse(1))
    """,
    "isinstance_forms": """
        class A:
            pass
        class B(A):
            pass
        class C:
            pass
        b = B()
        print(isinstance(b, (A, C)), isinstance(b, (C,)), isinstance(b, ()))
        print(isinstance(1, (str, int)), isinstance(1.0, (str, int)))
        print(isinstance(True, (int,)), isinstance('x', (bytes, str)))
        print(isinstance([1], (list, tuple)))
        print(isinstance(ValueError('x'), (KeyError, ValueError)))
        # A TUPLE HELD IN A VARIABLE is the same question as a literal one.
        kinds = (A, C)
        alias = A
        print(isinstance(b, kinds), isinstance(1, kinds), isinstance(b, alias))
        def kind(node):
            if isinstance(node, (A, B)):
                return 'ab'
            return 'other'
        print(kind(b), kind(C()))
    """,
    "generator_return_values": """
        # `return v` in a generator becomes StopIteration.value -- and that is
        # what `yield from` reads as the delegated generator's answer.
        def gen():
            x = yield 1
            y = yield x
            return (x, y)
        g = gen()
        print(next(g), g.send('a'))
        try:
            g.send('b')
        except StopIteration as e:
            print(e.value)
        def bare():
            yield 1
        b = bare()
        next(b)
        try:
            next(b)
        except StopIteration as e:
            print(repr(e), e.value)
        def inner():
            yield 'i'
            return 'from-inner'
        def outer():
            got = yield from inner()
            yield got
        print(list(outer()))
    """,
    "type_constructors_and_codepoints": """
        # Constructors reached through the TYPE: no receiver to be the first
        # argument, which is what separates these from `str.lower`.
        print(dict.fromkeys(['a', 'b'], 0))
        print((255).to_bytes(2, 'big'), int.from_bytes(bytes([1, 0]), 'big'))
        print(bytes.fromhex('01ff'))
        d = {'a': 1}
        d.update(b=2)
        d.update({'c': 3}, d=4)
        print(sorted(d.items()))
        # A str is UTF-8 underneath, so `chr` builds one to four bytes and
        # `ord` decodes them -- both counting CHARACTERS, not bytes.
        print(len(chr(233)), len(chr(0x4e2d)), ord(chr(233)), ord(chr(0x4e2d)))
        print(chr(233) == chr(233), chr(65), len('a' + chr(233)))
    """,
    "lazy_builtin_cursors": """
        # `map`, `filter`, `enumerate` and `zip` are CURSORS, not lists: the
        # function runs when the result is walked, and each is consumed once.
        log = []
        def keep(v):
            log.append(v)
            return v % 2 == 0
        f = filter(keep, [1, 2, 3, 4])
        print(log)
        print(list(f), log, list(f))
        m = map(str, [1, 2])
        print(list(m), list(m))
        e = enumerate('ab')
        print(next(e), list(e), list(e))
        print(type(enumerate([])).__name__, type(map(str, [])).__name__)
        print(type(filter(None, [])).__name__, type(zip()).__name__)
        print(list(filter(None, [0, 1, '', 'a', [], [1]])))
        print(list(zip([1, 2], 'ab')), list(zip()), list(zip([1, 2, 3], 'ab')))
        print(list(enumerate('ab', 5)))
        # A cursor over an INFINITE generator, stepped by hand.
        def naturals():
            n = 0
            while True:
                yield n
                n += 1
        doubled = map(lambda v: v * 2, naturals())
        print(next(doubled), next(doubled), next(doubled))
        print(sum(map(int, ['1', '2', '3'])), sorted(filter(keep, [3, 2, 1])))
    """,
    "lazy_iteration": """
        # ADVANCE UNTIL DONE, not walk by index. A generator has no length
        # until it has been run, and a body that appends to the list it is
        # walking sees the new elements -- neither works under an index walk.
        def naturals():
            n = 0
            while True:
                yield n
                n += 1
        for v in naturals():
            if v > 3:
                break
            print('lazy', v)
        xs = [1, 2, 3]
        seen = []
        for v in xs:
            seen.append(v)
            if len(seen) == 1:
                xs.append(99)
        print(seen, xs)
        shrinking = [1, 2, 3, 4]
        walked = []
        for v in shrinking:
            walked.append(v)
            shrinking.pop()
        print(walked, shrinking)
        for a, b in [(1, 'a'), (2, 'b')]:
            print(a, b)
        for k in {'x': 1, 'y': 2}:
            print(k)
        for c in 'ab':
            print(c)
        for v in {5}:
            print(v)
        for x in [1, 2, 3]:
            if x == 2:
                continue
            print('c', x)
        else:
            print('else')
        class Manual:
            def __init__(self):
                self.i = 0
            def __iter__(self):
                return self
            def __next__(self):
                if self.i >= 2:
                    raise StopIteration
                self.i += 1
                return self.i
        for v in Manual():
            print('manual', v)
        class Old:
            def __getitem__(self, i):
                if i >= 2:
                    raise IndexError
                return i * 5
        for v in Old():
            print('old', v)
    """,
    "generators": """
        def counter(n):
            i = 0
            while i < n:
                yield i
                i += 1
        print(list(counter(4)))
        for v in counter(3):
            print('v', v)
        # `send` -- the yield EXPRESSION's value is what was sent in.
        def echo():
            got = yield 'first'
            while got != 'stop':
                got = yield ('got', got)
            yield 'done'
        e = echo()
        print(next(e), e.send('a'), e.send('b'), e.send('stop'))
        # NONE OF THE BODY RUNS until the first `next`.
        def lazy():
            print('side')
            yield 1
        g = lazy()
        print('made')
        print(next(g))
        def withret():
            yield 1
            return
            yield 2
        print(list(withret()))
        # A `for` INSIDE a generator: its index lives in the frame, because a
        # register does not survive the return a `yield` compiles to.
        def nested():
            for x in [1, 2]:
                for y in 'ab':
                    yield (x, y)
        print(list(nested()))
        h = counter(9)
        print(next(h))
        h.close()
        try:
            next(h)
        except StopIteration:
            print('closed')
        print(sum(counter(5)), sorted(counter(3), reverse=True))
        try:
            next(iter(counter(0)))
        except StopIteration:
            print('empty')
        # `yield from` -- delegation, including recursively.
        def inner():
            yield 1
            yield 2
        def outer():
            yield 0
            yield from inner()
            yield 3
        print(list(outer()))
        def flat(xs):
            for x in xs:
                if isinstance(x, list):
                    yield from flat(x)
                else:
                    yield x
        print(list(flat([1, [2, [3, 4]], 5])))
        # `throw` raises AT the suspension point, so a `try` in the body
        # catches it; `close` sends GeneratorExit, so a `finally` runs.
        def plain():
            yield 1
            yield 2
        p = plain()
        print(next(p))
        try:
            p.throw(ValueError('boom'))
        except ValueError as e:
            print('ValueError', e)
        def guarded():
            try:
                yield 1
                yield 2
            except ValueError:
                yield 'caught'
        q = guarded()
        print(next(q), q.throw(ValueError('x')))
        def cleaning():
            try:
                yield 1
                yield 2
            finally:
                print('cleanup')
        c = cleaning()
        print(next(c))
        c.close()
        class Tracked:
            def __enter__(self):
                print('enter')
                return self
            def __exit__(self, *a):
                print('exit')
                return False
        def held():
            with Tracked():
                yield 1
                yield 2
        hh = held()
        print(next(hh), next(hh))
        try:
            next(hh)
        except StopIteration:
            print('stop')
    """,
    "comprehension_scope_and_print": """
        # A COMPREHENSION HAS ITS OWN SCOPE: the outer name is untouched, and
        # one only the comprehension binds is unbound afterwards.
        i = 'outer'
        squares = [i * 2 for i in range(3)]
        print(squares, i)
        gen = list(j for j in range(2))
        print(gen)
        try:
            print(j)
        except NameError:
            print('NameError')
        # A WALRUS writes the ENCLOSING scope, which is the difference.
        total = 0
        sums = [total := total + n for n in (1, 2, 3)]
        print(sums, total)
        def inner():
            k = 'local'
            got = [k for k in range(2)]
            return got, k
        print(inner())
        print('a', 'b', sep='-', end='!')
        print()
        print('x', 'y', sep='')
        print(1, 2, 3, sep=', ')
        for at, ch in enumerate('ab', start=10):
            print(at, ch)
        for at, ch in enumerate('ab', 5):
            print(at, ch)
    """,
    "importing_math": """
        import math
        import math as m
        from math import pi, sqrt
        from math import pi as PI
        print(round(math.pi, 5), round(m.pi, 5), round(pi, 5), round(PI, 5))
        # A module is built ONCE per program, so the two names are one object.
        print(math is m)
        print(math.floor(-2.5), math.ceil(-2.5), math.trunc(-2.5))
        print(math.trunc(2.5), math.gcd(12, 18), math.lcm(4, 6))
        print(math.isqrt(17), math.factorial(5), round(math.log(math.e), 10))
        print(math.inf > 0, math.isnan(math.nan), math.isfinite(1.0))
        print(math.isclose(1.0, 1.0 + 1e-12), math.isclose(1.0, 1.1, rel_tol=0.2))
        print(math.copysign(1.0, -0.0), sqrt(9), math.pow(2, 3))
        print(sorted([4.0, 1.0, 9.0], key=math.sqrt))
        print(round(math.tau, 5) == round(2 * math.pi, 5))
    """,
    "unpacking_arity_and_except_target": """
        # THE ARITY IS CHECKED BEFORE ANYTHING IS BOUND. A short sequence read
        # past the end and reported an IndexError from a subscript the program
        # never wrote; a long one bound the leading names and silently dropped
        # the rest, which is the worse of the two.
        try:
            a, b = [1]
        except ValueError as e:
            print(str(e))
        try:
            a, b = [1, 2, 3]
        except ValueError as e:
            print(str(e))
        try:
            p, *q = []
        except ValueError as e:
            print(str(e))
        a, b = [1, 2]
        (c, d), f = (1, 2), 3
        x, *rest = [1, 2, 3]
        print(a, b, c, d, f, x, rest)

        # `except ... as e` DELETES `e` when the clause ends, and a program
        # reads that back: `e` afterwards is a NameError, not the caught value.
        try:
            raise ValueError("x")
        except ValueError as e:
            print(type(e).__name__)
        try:
            print(e)
        except NameError:
            print("NameError")

        # A handler that RETURNS or RAISES has already ended its block, so
        # there is nothing to delete and nowhere to emit the deletion.
        def returns_from_handler():
            try:
                raise ValueError("x")
            except ValueError as e:
                return str(e)

        def raises_from_handler():
            try:
                raise KeyError("k")
            except KeyError as e:
                raise RuntimeError("wrapped")

        print(returns_from_handler())
        try:
            raises_from_handler()
        except RuntimeError as e:
            print(str(e))
    """,
    "name_resolution_and_nan": """
        # A CONTAINER ASKS "is this the same object?" before it asks the
        # object, which is why `[nan] == [nan]` is True while `nan == nan` is
        # False. Membership and dict comparison rest on the same rule.
        nan = float("nan")
        print(nan == nan, nan != nan)
        print([nan] == [nan], nan in [nan], {"k": nan} == {"k": nan})
        print(sorted([1.0, nan, 2.0]) == [1.0, nan, 2.0])

        # LOCAL, THEN GLOBAL, THEN BUILTINS. Shadowing a builtin at module
        # level made every use of that name a global read, so `del` left it
        # raising NameError for a name that is always defined.
        print(len([1, 2]))
        len = 5
        print(len)
        del len
        print(len([1, 2]))

        # `print(*xs, sep=)` -- the starred form builds its arguments at run
        # time and went through a path with nowhere to put the separator,
        # which it dropped rather than refusing.
        print(*[1, 2, 3], sep=",")
        print(*[1, 2], sep="-", end="!")
        print("")
        print("a", "b", sep="-")
        print(*[], sep=",")
    """,
    "metaclasses": """
        # `class Meta(type)` gets a REAL BASE -- the runtime's `type` class,
        # whose dict holds the two natives `super()` reaches -- so a
        # metaclass's `__new__` builds a class through the same path any
        # other `__new__` takes.
        log = []

        class Meta(type):
            def __new__(mcls, name, bases, ns, **kw):
                log.append(("new", name, sorted(kw.items())))
                return super().__new__(mcls, name, bases, ns)
            def __init__(cls, name, bases, ns, **kw):
                log.append(("init", name))
                super().__init__(name, bases, ns)

        class C(metaclass=Meta, flavour="x"):
            pass

        print(log)
        # `type(C)` IS THE METACLASS. An ordinary class reads as `type`.
        print(type(C).__name__)

        class Plain:
            pass

        print(type(Plain).__name__)

        # PEP 3115: the body runs into whatever `__prepare__` supplies, so a
        # seeded mapping is visible on the class and the body's own bindings
        # are readable as a mapping before the class exists.
        class OrderedMeta(type):
            @classmethod
            def __prepare__(mcls, name, bases, **kw):
                return {"seeded": 7}
            def __new__(mcls, name, bases, ns):
                cls = super().__new__(mcls, name, bases, dict(ns))
                cls.declared = [k for k in ns if not k.startswith("_")]
                return cls

        class D(metaclass=OrderedMeta):
            b = 1
            a = 2

        print(D.declared, D.b, D.a, D.seeded, type(D).__name__)

        # THE METACLASS DECIDES both checks, and both answer a BOOL whatever
        # the hook returned.
        class Quacky(type):
            def __instancecheck__(cls, obj):
                return "quacks"
            def __subclasscheck__(cls, sub):
                return True

        class Duck(metaclass=Quacky):
            pass

        print(isinstance(42, Duck), issubclass(int, Duck))
        print(isinstance(42, int), isinstance("a", int))

        # `type(name, bases, ns)` is the `class` statement written out, and
        # builds the same object a metaclass's `super().__new__` does.
        class B:
            def m(self):
                return "b"

        X = type("X", (), {"a": 1})
        Y = type("Y", (B,), {})
        print(X.a, X.__name__, type(X).__name__)
        print(Y().m(), isinstance(Y(), B), type(3).__name__)

        # `object` is the ROOT of every chain even though no class links to
        # it, so a class with no written base still has one base. The empty
        # tuple said the chain stopped at the class.
        print(B.__base__.__name__, len(B.__bases__), B.__bases__[0].__name__)

        class Deriv(B):
            pass

        print(Deriv.__base__ is B, len(Deriv.__bases__))
    """,
    "new_and_object_defaults": """
        # `__new__` was IGNORED -- the instance was allocated and the method
        # never ran -- and `super().__init__()` in a class with no explicit
        # base was an AttributeError, because `object`'s defaults existed as
        # behaviours and no VALUE named them.
        class P:
            def __new__(cls, *a):
                print("new", a)
                return super().__new__(cls)
            def __init__(self, n):
                print("init", n)
                self.n = n

        print(P(5).n)

        # A class attribute BOUND TO NONE is a real attribute. The lazily
        # filled slot every singleton starts from was invisible.
        class Singleton:
            _one = None
            def __new__(cls):
                if cls._one is None:
                    cls._one = super().__new__(cls)
                return cls._one

        print(Singleton() is Singleton())

        # `__init__` RUNS ONLY IF `__new__` RETURNED ONE OF THESE.
        class Weird:
            def __new__(cls):
                return 42
            def __init__(self):
                print("never")

        print(Weird())

        class Base:
            def __init__(self):
                super().__init__()
                self.tag = "base"

        class Sub(Base):
            def __init__(self):
                super().__init__()
                self.tag = self.tag + "+sub"

        print(Sub().tag)

        class E:
            def __eq__(self, o):
                return super().__eq__(o)
            def __hash__(self):
                return super().__hash__()

        e = E()
        print(e == e, e == E(), type(hash(e)).__name__)
        # NOT the default repr's text: CPython qualifies it with the module
        # (`<__main__.R object ...>`) and no qualified name is recorded here.
        # What is pinned is that `super().__repr__()` reaches the DEFAULT
        # rather than being rewritten to the repr of the super object.
        class R:
            def __repr__(self):
                return "R<" + super().__repr__()[0] + ">"

        print(str(R()), repr(R()))
    """,
    "bound_methods_compare_by_receiver": """
        # A BOUND METHOD IS A FRESH OBJECT PER ACCESS, so `c.m is c.m` is
        # False -- and two of them are EQUAL when they wrap the same function
        # and the same receiver. Only bound ones: two closures over one `def`
        # are distinct objects, and CPython calls those unequal.
        class C:
            def m(self):
                return 1

        c, d = C(), C()
        print(c.m == c.m, c.m is c.m, C.m is C.m)
        print(c.m == d.m, c.m == C.m, [c.m] == [c.m])

        def outer():
            def inner():
                pass
            return inner

        print(outer() == outer(), outer() is outer())
        held = c.m
        print(held == c.m, held() == 1, len({c.m, c.m}))
    """,
    "equality_is_never_a_pointer_accident": """
        # Every kind that is not a number used to fall through to the NUMERIC
        # comparison, which reads a union member that for most kinds is a
        # pointer. Two slices sharing a `start`, two views onto one dict and
        # two memoryviews over one buffer each compared EQUAL because the
        # pointers matched. What this pins is that each kind either compares
        # by content deliberately or by identity, and never by accident.
        d = {"a": 1}
        print(d.keys() == {"a"}, d.items() == {("a", 1)}, d.keys() == {"b"})
        # `values()` is the one view that is not set-like: it defines no
        # equality, so it is equal only to itself.
        v = d.values()
        print(d.keys() == d.values(), v == v, d.keys() == d.keys())
        print(memoryview(b"ab") == b"ab", memoryview(b"ab") == b"ac")
        ba = bytearray(b"abcd")
        print(memoryview(ba)[0:2] == memoryview(ba)[0:3])
        print(slice(1, 2) == slice(1, 3), slice(1, 2) == slice(1, 2))
        print({1} == frozenset({1}), 1 == 1.0, None == None, [1] == [1])
    """,
    "keyword_only_parameters": """
        # A KEYWORD-ONLY PARAMETER TAKES NO ARGUMENT POSITION. Three places
        # treated it as though it did: the arity check refused
        # `b(1, 2, c=9)`, the call lowering let the `2` land in `c`, and the
        # runtime binder sent `c=3` into `**kw` whenever there were surplus
        # positionals bound for `*args` -- so `c` kept its default and the
        # tuple came back empty.
        def f(a, b=2, *args, c=3, **kw):
            return (a, b, args, c, kw)

        print(f(1), f(1, b=9))
        print(f(1, 2, 3, c=4, d=5), f(1, 2, 3, 4), f(1, 2, nope=1, c=2))

        def g(a, /, b, *, c):
            return (a, b, c)

        print(g(1, 2, c=3), g(1, b=2, c=3))
        # A POSITIONAL-ONLY PARAMETER CANNOT BE NAMED. The runtime binder
        # refused this for a call through a value; a DIRECT call resolves
        # names at the call site and filled the slot anyway, so `f(a=1)`
        # against `def f(a, /)` quietly worked.
        try:
            g(a=1, b=2, c=3)
        except TypeError:
            print("positional-only")
        # FEWER positionals than there are positions, with a keyword-only
        # tail behind them -- the slot list has to reach the tail so its
        # defaults land, and sizing it from the arguments given did not.
        def few(a, b=2, *, c=3, d=4):
            return (a, b, c, d)

        print(few(1), few(1, 5), few(1, d=9), few(1, 5, c=8, d=9))

        def required_kwonly(x, *args, c):
            return (x, args, c)

        print(required_kwonly(1, 2, c=3), required_kwonly(1, c=2))

        def h(*args, **kw):
            return (args, kw)

        print(h(), h(1, 2, x=3))

        class K:
            def __init__(self, a, *rest, b=1, **kw):
                self.v = (a, rest, b, kw)

        print(K(1, 2, b=3, z=4).v, K(1).v)
        # NOT a try/except for the missing `c`: a provably missing required
        # argument is REFUSED at compile time here, as every provable arity
        # mistake is, so there is no run to catch it in.
    """,
    "exception_repr": """
        # `str(KeyError('k'))` is the REPR of the key -- KeyError alone does
        # that, so a missing key whose text is empty is still visible. The
        # trap is a KeyError REBUILT from a failed lookup: its argument is
        # already the repr, and repr'ing it again said `KeyError("'k'")`.
        print(repr(KeyError("k")), str(KeyError("k")))
        print(repr(ValueError("v")), str(ValueError("v")), repr(KeyError()))
        try:
            {}["k"]
        except KeyError as e:
            print(repr(e), str(e))
        try:
            [][0]
        except IndexError as e:
            print(repr(e), str(e))
        print([KeyError("k"), ValueError("v")])
    """,
    "percent_formatting": """
        # Translated into the format mini-language rather than reimplemented:
        # `%05.2f` and `{:05.2f}` mean the same thing. What is pinned here is
        # the printf SPELLING -- which flags mean what, and the two
        # conversions the mini-language has no type character for.
        class P:
            def __init__(self, n):
                self.n = n
            def __repr__(self):
                return "P!" + str(self.n)
            def __str__(self):
                return "p" + str(self.n)

        print("%d %s" % (1, "a"))
        print("%05.2f|%x|%X|%o|%e" % (3.14159, 255, 255, 8, 1234.5))
        # A USER OBJECT through `%s` and `%r` -- the reason Python's own `%`
        # is not what does this: it would print an address.
        print("%r %s" % (P(1), P(2)))
        print("%-6d|%+d|% d|%#x|%08.3f" % (42, 5, 5, 255, 3.14159))
        print("%s" % [1, 2], "%s" % (1,), "%s" % "x", "%%")
        print("%c%c" % (65, "B"), "%.3s" % "abcdef", "%5s|" % "ab")
        # `b"%s"` inserts THE BYTES, not their repr, and the answer is bytes.
        print(b"%d %s %x" % (3, b"ab", 255), type(b"%d" % 3).__name__)
        try:
            "%d %d" % (1,)
        except TypeError:
            print("too few")
        try:
            "%d" % (1, 2)
        except TypeError:
            print("too many")
        # A MAPPING ON THE RIGHT supplies NAMED fields only, and nothing is
        # consumed positionally -- so an unused entry is ordinary rather than
        # "not all arguments converted".
        print("ab" % {"ab": 1}, "%(x)s%(y)s" % {"x": "a", "y": "b"})
        print("%(n)05.1f|" % {"n": 3.14159})
        try:
            "%(z)s" % {"a": 1}
        except KeyError:
            print("KeyError")
    """,
    "definition_time_defaults_and_decorators": """
        # A module-level `def` STATEMENT runs where it is written. Its
        # defaults and its decorators were evaluated at program start instead,
        # before the module body had bound anything -- so a default naming a
        # global was a NameError for a program CPython runs, and a decorator
        # naming one was too.
        log = []

        def outer(fn):
            log.append("outer")
            return fn

        def inner(fn):
            log.append("inner")
            return fn

        @outer
        @inner
        def decorated():
            pass

        print(log)

        n = 1

        def f(v=n):
            return v

        n = 99
        print(f(), f(5), n)
    """,
    "bundled_warnings": """
        # The WARNING CATEGORIES are exceptions like any other -- `Warning`
        # inherits `Exception` -- and were missing from the hierarchy
        # entirely, so `issubclass(DeprecationWarning, Warning)` could not
        # even be asked.
        print(issubclass(DeprecationWarning, Warning), UserWarning.__name__)
        try:
            raise DeprecationWarning("d")
        except Warning as e:
            print(type(e).__name__, str(e))

        # A COMPILED PROGRAM HAS NO WARNING FILTERS TO INHERIT, so the module
        # is the whole mechanism rather than a view onto one: an action, a
        # place to record, and a context manager that saves and restores both.
        import warnings
        from warnings import deprecated

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            warnings.warn("dep", DeprecationWarning)
            warnings.warn("usr", UserWarning)
        print(sorted(w.category.__name__ for w in caught))

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("ignore")
            warnings.warn("dep", DeprecationWarning)
        print(len(caught))

        # The action is restored on the way OUT, which is what makes this
        # usable around code expected to fail.
        with warnings.catch_warnings(record=True) as after:
            warnings.warn("again", UserWarning)
        print(len(after))

        @deprecated("use g instead")
        def f():
            return 1

        print(f(), f.__deprecated__)
    """,
    "bundled_itertools_and_contextlib": """
        # Two more modules written in PYTHON and spliced in. `itertools` is
        # all generators, so the laziness is the language's rather than a data
        # structure imitating it; `contextmanager` is a generator wearing the
        # `with` protocol, and written here that sentence IS the
        # implementation.
        import itertools
        import contextlib

        print(list(itertools.chain([1], [2, 3])))
        print(list(itertools.islice(itertools.count(5), 3)))
        print(list(itertools.islice(range(10), 2, 5)))
        print(list(itertools.repeat("x", 3)))
        print([list(g) for _, g in itertools.groupby([1, 1, 2])])
        print(list(itertools.product([1, 2], "ab")))
        # THE ORDER IS PART OF THE ANSWER: the right tuples in the wrong order
        # is still wrong to a program that prints them.
        print(list(itertools.combinations([1, 2, 3], 2)))
        print(list(itertools.combinations([1], 2)), list(itertools.chain()))

        @contextlib.contextmanager
        def tracked(log):
            log.append("enter")
            yield log
            log.append("exit")

        log = []
        with tracked(log) as held:
            held.append("body")
        print(log)

        # A `try` around the `yield` is how the block's exception reaches the
        # generator, and swallowing it there SUPPRESSES it.
        @contextlib.contextmanager
        def swallow():
            try:
                yield
            except ValueError:
                pass

        with swallow():
            raise ValueError("x")
        print("suppressed")
    """,
    "bundled_functools": """
        # `functools` is written in PYTHON and spliced into the program that
        # imports it -- see `frontends/python/bundled.py`. Compiling the
        # standard library with the compiler under test is the point: it
        # cannot drift from the semantics it copies, because it IS them.
        #
        # It also found two compiler bugs on its first day, both pinned below.
        import functools
        from functools import reduce, wraps

        print(functools.reduce(lambda a, b: a + b, [1, 2, 3]))
        # NOT `reduce(max, ...)`: a builtin passed as a VALUE becomes a
        # one-argument thunk, so a two-argument builtin cannot be one.
        # A lambda can, which is what this uses.
        print(reduce(lambda a, b: a + b, [1, 2, 3], 10))

        def add(a, b, c=0):
            return a + b + c

        # A NESTED FUNCTION CAPTURING `*args`: the vararg arrives in a
        # register like any parameter, but was not marked as one, so boxing it
        # for a closure built the cell from None and threw the tuple away.
        # `**kw` worked, which is what made it look like something else.
        def outer(*a, **k):
            def inner():
                return a, k
            return inner()

        print(outer(1, 2, x=3), outer())

        # `f(*xs, **kw)` DROPPED EVERY KEYWORD. The keyword half has to travel
        # separately, as it does for every other call shape -- appended to the
        # argument list it would arrive as one more positional.
        args = (1, 2)
        kw = {"c": 3}
        print(add(*args, **kw), add(*args), add(1, 2, **kw))

        p = functools.partial(add, 1)
        q = functools.partial(add, c=10)
        print(p(2), p(2, c=3), q(1, 2), p.func is add, p.args)

        @functools.total_ordering
        class V:
            def __init__(self, n):
                self.n = n
            def __eq__(self, o):
                return self.n == o.n
            def __lt__(self, o):
                return self.n < o.n

        print(V(1) < V(2), V(2) >= V(1), V(1) <= V(1), V(2) > V(1))

        @wraps(add)
        def wrapper(*a, **k):
            return add(*a, **k)

        print(wrapper.__name__, wrapper(1, 2), wrapper.__wrapped__ is add)
    """,
    "await_in_expression_position": """
        # A REGISTER DOES NOT SURVIVE A SUSPENSION -- that is why a
        # generator's locals live in frame slots -- and neither does an
        # INTERMEDIATE. `await a() + await b()` computed the left operand into
        # a register, the right operand suspended, and the resume path read a
        # register no path had written: invalid IR, reported against a block
        # the program never wrote. Every display holding an accumulator across
        # its elements had the same shape.
        import asyncio

        async def v(x):
            return x

        async def main():
            total = await v(1) + await v(2)
            xs = [await v(n) for n in (1, 2)]
            d = {await v(1): await v(2)}
            s = {await v(3)}
            trio = (await v(1), await v(2), await v(3))
            nested = [await v(n) + await v(n) for n in (1, 2)]
            return total, xs, d, s, trio, nested

        print(asyncio.run(main()))

        # A frame slot holds the HANDLE, not the object: a tuple under
        # construction is REPLACED in its cell as it grows, and a slot holding
        # the object froze at the empty tuple it went in as.
        async def builds():
            return (await v(1), await v(2), await v(3))

        print(asyncio.run(builds()))
    """,
    "chained_assignment_and_bare_return": """
        # `a = b = value` -- ONE evaluation, bound to each target left to
        # right, which is what makes `a = b = []` two names for the SAME list.
        a = b = [1]
        a.append(2)
        print(a, b, a is b)
        p = q = r = 5
        print(p, q, r)
        d1 = {}
        d1["k"] = n = 7
        print(d1, n)
        x, y = 1, 2
        x, y = y, x
        print(x, y)

        # A BARE `return` in a dynamic function yields None AS AN OBJECT.
        # Typing it as the static None made `def f(): return` -- and every
        # early exit written that way -- a narrowing error for a program
        # CPython runs.
        def bare():
            return

        def falls():
            pass

        def early():
            for _ in range(1):
                return "early"

        print(bare(), falls(), early())
    """,
    "exception_group_message": """
        # `g.message` is the text a group was built with -- its FIRST argument,
        # separate from the exceptions it carries, and present only on a group.
        inner = ExceptionGroup("inner", [TypeError("t"), ValueError("v")])
        outer = ExceptionGroup("outer", [inner])
        print(sorted(type(e).__name__ for e in inner.exceptions))
        print(outer.message, len(inner.exceptions))
        try:
            ValueError("v").message
        except AttributeError:
            print("AttributeError")
    """,
    "function_introspection": """
        # `__defaults__` is the POSITIONAL defaults as a tuple and
        # `__kwdefaults__` the keyword-only ones as a dict -- each NONE rather
        # than empty when there are none, which is how a program tells "no
        # defaults" from "a default that is falsey". They are stored as one
        # trailing run, keyword-only last, so the split is the number of
        # keyword-only parameters that have one.
        def f(a, b=1, *args, c=2, **kw):
            '''Doc.'''
            return a

        print(f.__defaults__, f.__kwdefaults__)
        print(f.__name__, f.__doc__, f.__qualname__)

        def g(x):
            return x

        def h(a, b=1, d=2):
            return a

        print(g.__defaults__, g.__kwdefaults__)
        print(h.__defaults__, h.__kwdefaults__)

        # PEP 3155: the frontend's own key for a function is already CPython's
        # spelling of the qualified name.
        class C:
            def m(self):
                pass

        print(C.__qualname__, C.m.__qualname__, C.m.__name__)
        # PEP 649 for a CLASS: a body's ANNOTATED NAMES build
        # `C.__annotations__` the same lazy way a function's parameters do,
        # and a name with NO value still appears -- which is the whole point
        # of writing `a: int` on its own.
        class Ann:
            a: int = 1
            b: str

        print(sorted(Ann.__annotations__), Ann.a)

        class NoAnn:
            pass

        print(NoAnn.__annotations__)
        # A function is an object a program may hang anything on.
        f.custom = "attached"
        print(f.custom)
        # `f.__code__` -- ENOUGH OF ONE to answer what a program asks a
        # function about its own signature. There is no bytecode here to
        # describe; these are what introspection actually reads.
        print(f.__code__.co_argcount, f.__code__.co_varnames[:2])
        print(f.__code__.co_kwonlyargcount, f.__code__.co_name)
    """,
    "membership_consumes_a_generator": """
        # `x in gen` CONSUMES the generator up to the match and leaves the
        # rest -- a generator is consumed once. Draining it to a list to
        # answer the question reported the generator as not iterable at all.
        print(sum(v for v in range(3)))
        squares = (v * v for v in range(3))
        print(2 in squares)
        print(list(squares))
        g2 = (v for v in range(4))
        print(1 in g2, list(g2))
        # A container is unaffected -- it has no position to consume.
        xs = [1, 2, 3]
        print(2 in xs, xs, 9 in xs)
    """,
    "slots_conflict_and_descriptor": """
        # `__slots__ = ("v",)` and `v = 1` IN THE SAME BODY is a ValueError at
        # class creation: the slot and the attribute would share a name and
        # the attribute would win silently. RAISED, not refused at compile
        # time -- a program may catch it, and this one does.
        try:
            class Bad:
                __slots__ = ("v",)
                v = 1
        except ValueError:
            print("ValueError")

        class Ok:
            __slots__ = ("v",)

        o = Ok()
        o.v = 1
        print(o.v, Ok.__slots__)
        # A SLOT READ THROUGH THE CLASS is a descriptor, not a missing
        # attribute: `__slots__` declares storage and the class dict holds
        # nothing for it.
        print(type(Ok.v).__name__)
        try:
            o.other = 2
        except AttributeError:
            print("AttributeError")
    """,
    "bytes_methods_mirror_str": """
        # bytes shares the str LAYOUT -- a pointer and a length -- so every
        # one of these is the same operation. What was missing is that the
        # receiver check rejected the kind and that the RESULT has to come
        # back tagged bytes, which one wrapper at the call site does for all
        # fifty-odd methods at once.
        b = b"  Hello  "
        print(b.strip(), b.upper(), b.lower())
        print(b.split(), b.replace(b"l", b"L"), b.find(b"e"))
        print(b"a,b".split(b","), b"ab".startswith(b"a"), b"ab".endswith(b"b"))
        print(b"-".join([b"a", b"b"]), b"ab".index(b"b"), b"aa".count(b"a"))
        print(b"ab".ljust(4), b"ab".partition(b"b"), b"AB".title())
        # A METHOD THAT ANSWERS AN INT is left alone, which is what makes the
        # wrapper safe to apply everywhere.
        print(b"abc".find(b"z"), len(b"abc"), b"abc"[1])
        # str is untouched -- the wrapper returns its argument for any
        # receiver that is not bytes.
        s = "  Hi  "
        print(s.strip(), s.upper(), s.split(), "a,b".split(","))
        print("x".join(["1", "2"]), "abc".find("b"), "abc".partition("b"))
        print([1, 2, 1].count(1), [1, 2].index(2))
        # THE CONVERSIONS ARE NOT MIRRORED. `b.hex()` and `b.decode()` answer
        # a str FROM bytes -- that is what they are for -- so re-tagging their
        # result would undo the conversion the program asked for. Wrapping
        # every method without excluding these two made `b.hex()` bytes.
        raw = bytes([1, 255, 16])
        print(raw.hex(), raw.hex(":"), type(raw.hex()).__name__)
        print(b"ab".decode(), "ab".encode(), bytes.fromhex("01ff10"))
        # `bytes(3)` is THREE ZERO BYTES, not the digit three.
        print(bytes(3), list(raw), bytes([1, 2]))
        # MIXING BYTES AND str IS A TypeError -- PEP 3112's whole point. An
        # equality body ended up in `apy_add`'s mixed branch during this work
        # and returned a C int as a value, so this SEGFAULTED rather than
        # raising. Nothing else in the corpus added the two kinds.
        try:
            b"a" + "a"
        except TypeError:
            print("TypeError")
        print(b"a" == "a", b"ab" + b"c", "ab" + "c")
        print(b"ab"[0], "ab"[0], len(b"ab"), len("ab"))
    """,
    "complex_strings_and_signed_zero": """
        # `complex("1+2j")` is a PARSE, not an arithmetic conversion, and the
        # TypeError it used to raise already promised a string was acceptable.
        print(complex("1+2j"), complex("2j"), complex("3"), complex("-1-1j"))
        print(complex("(1+2j)"), complex(1, 2), complex(1.5))
        try:
            complex("bad")
        except ValueError:
            print("ValueError")

        # PEP 682: `z` turns a negative zero into a positive one, and it is
        # about the ROUNDED value -- so `-0.001` at one decimal place is `0.0`
        # too. `signbit`, not `< 0`: negative zero is not less than zero, and
        # it is the value the flag exists for.
        print(format(-0.0, "z.1f"), format(-0.0, ".1f"), format(0.0, "z.1f"))
        print(format(-0.001, "z.1f"), f"{-0.0:z.2f}", format(-1.5, "z.1f"))
    """,
    "class_body_order_and_slice_del": """
        # A class body runs TOP TO BOTTOM, attributes and methods together.
        # They were lowered in two passes, so an attribute could not see a
        # method defined above it and the class dict came out in pass order
        # rather than definition order -- which PEP 520 makes observable.
        class C:
            b = 1
            a = 2

            def m(self):
                return self.b

            z = 3

        print([k for k in vars(C) if not k.startswith("_")])

        class D:
            def f(self):
                return 1
            g = f
            x = 2

        print(D().g(), D.x, [k for k in vars(D) if not k.startswith("_")])

        # A CLASS BODY IS A BLOCK THAT RUNS, once, where it is written -- and
        # a statement that binds nothing still has its effect, in its own
        # place among the rest. Refusing a bare expression rejected the
        # program over a statement that binds nothing.
        log = []

        class E:
            log.append("body")
            v = 1
            log.append("after v")

            def m(self):
                return self.v

        print(log)
        E()
        E()
        print(log, E().m(), E.v)

        # `del xs[1:3]` REMOVES A SPAN. It fell through to the index path,
        # which asked for an integer, got the slice, and reported an
        # IndexError about a subscript the program never wrote.
        xs = [0, 1, 2, 3]
        del xs[1:]
        ys = [0, 1, 2, 3]
        del ys[1:3]
        zs = [0, 1, 2]
        del zs[:]
        ws = [0, 1, 2]
        del ws[1]
        print(xs, ys, zs, ws)
    """,
    "annotations_are_lazy": """
        # PEP 649: `__annotations__` is BUILT ON ACCESS, by a thunk the `def`
        # records. Evaluating them at the `def` would make
        # `def f(x: Undefined)` an error where Python accepts it -- only
        # READING them is, and that is the whole point of the PEP.
        def f(a: int, b: "str" = "x") -> bool:
            return True

        print(sorted(f.__annotations__), f.__annotations__["a"], f(1))

        def plain(x):
            return x

        print(plain.__annotations__, plain(2), hasattr(plain, "__annotate__"))

        def lazy(x: Undefined) -> AlsoUndefined:
            return x

        print(callable(lazy), lazy(1), hasattr(lazy, "__annotate__"))
        try:
            lazy.__annotations__
        except NameError:
            print("NameError")

        class C:
            def m(self, n: int) -> str:
                return "m"

        print(sorted(C.m.__annotations__), C().m(1))
    """,
    "generic_aliases_and_unions": """
        # PEP 604: `int | str` IS A TYPE, not an arithmetic operation, and its
        # repr uses the bars rather than `Union[...]`. The arms flatten, so
        # `int | str | float` is one three-armed union and `isinstance` over
        # it is a single walk.
        u = int | str
        print(u, isinstance(3, u), isinstance("a", u), isinstance(3.0, u))
        v = int | str | float
        print(v, isinstance(1.5, v), isinstance(None, u))

        # PEP 585: `list[int].__origin__` is `list` and `.__args__` is
        # `(int,)` -- the two a program reads off an annotation.
        t = list[int]
        print(t, t.__origin__ is list, t.__args__)
        print(dict[str, int], len(dict[str, int].__args__))

        # A generator's three methods are dispatched by NAME at the call site,
        # so nothing needed a VALUE for them -- until `hasattr(g, "close")`,
        # which every duck-typed consumer asks.
        def gen():
            yield 1

        g = gen()
        print(hasattr(g, "close"), hasattr(g, "throw"), hasattr(g, "send"))
        print(next(g), hasattr(g, "nosuch"))
    """,
    "generator_flow_rules": """
        # A `return` INSIDE A `finally` discards whatever was in flight. The
        # finally body ran, decided the function's answer, and the exception it
        # was cleaning up after went on propagating anyway.
        def wins():
            try:
                raise ValueError("lost")
            finally:
                return "finally wins"

        print(wins())

        def kept():
            try:
                raise ValueError("kept")
            finally:
                pass

        try:
            kept()
        except ValueError as e:
            print("ValueError", e)

        # PEP 479: a `StopIteration` that ESCAPES a generator body becomes a
        # RuntimeError with the original as its `__cause__`. Left alone it was
        # indistinguishable from the generator finishing normally, so a bug in
        # the body read as a clean end of iteration.
        def raises_stop():
            yield 1
            raise StopIteration("inner")

        g = raises_stop()
        print(next(g))
        try:
            next(g)
        except RuntimeError as e:
            print("RuntimeError", type(e.__cause__).__name__)
    """,
    "exception_classes_are_values": """
        # `raise` and `except` match on the NAME, so registering the name is
        # what makes an exception class work -- but a program also reads
        # `MyError.__mro__` and passes the class to `issubclass`, and binding
        # nothing made every such use a NameError for a class it just defined.
        class AppError(Exception):
            pass

        class SubError(AppError):
            pass

        try:
            raise SubError("boom")
        except AppError as e:
            print(type(e).__name__, str(e), isinstance(e, AppError))

        print(SubError.__mro__[1].__name__, issubclass(SubError, AppError))
        # An exception type's parent is in the NAME TABLE, not in a base
        # pointer, so the walk had to ask the table -- `Exception.__bases__`
        # answered `object` where CPython says `BaseException`.
        print(Exception.__bases__[0].__name__, issubclass(Exception, BaseException))
        print([c.__name__ for c in Exception.__mro__])

        class A:
            pass

        class B(A):
            pass

        print([c.__name__ for c in B.__mro__])
    """,
    "format_fields_and_module_dunders": """
        # `{x[0]}` and `{a.real}`: the NAME stops at the first `.` or `[`, and
        # what follows is a chain of accessors. Treating the whole field as one
        # keyword looked for an argument called `x[0]`.
        print("{x[0]}".format(x=[9]), "{a.real}".format(a=1.5))
        print("{p[1]}{p[0]}".format(p=("a", "b")), "{m[k]}".format(m={"k": 7}))
        print("{:{}}".format(42, ">6") + "|", "{0}{1}{0}".format("a", "b"))
        print("{{literal}}".format(), "{}{}".format("a", "b"))

        # `__file__` is the one module dunder whose value is not a constant of
        # every compilation, and `globals()` carries the dunders too --
        # `"__builtins__" in globals()` is a question programs ask.
        # NO MODULE DUNDERS HERE. This corpus runs CPython through `exec`
        # with a fresh globals dict, which has neither `__file__` nor
        # `__doc__` nor `__builtins__` -- so the reference would disagree with
        # itself rather than with us. `statements/module-dunder-attributes`
        # covers them, as a real script, which is the only way they mean
        # anything.
        print(__name__)
    """,
    "strings_are_measured_in_characters": """
        # A str is stored as UTF-8. `len` counted CHARACTERS while indexing,
        # slicing, iteration and `find` all counted BYTES -- identical for
        # ASCII and wrong for everything else, in BOTH paths equally, which is
        # why nothing caught it. `s[1]` was the first half of a character and
        # a `for` loop took its bound from one count and its elements from the
        # other, so it walked off the end.
        s = "héllö"
        print(len(s), [ord(c) for c in s])
        print(ord(s[0]), ord(s[1]), ord(s[-1]))
        print(len(s[1:4]), ord(s[1:4][0]), s[::-1] == "ölleéh")
        print(s.find("ll"), s.rfind("l"), s.index("l"), s.find("z"))
        print(s.find("l", 3), s.count("l"), len(s[::2]))
        # ASCII is unchanged, which is the whole reason this stayed hidden.
        a = "abc"
        print(a[0], a[-1], a[1:], a[::-1], len(a), list(a))
        print("abcabc".find("bc"), "abcabc".rfind("bc"), "abc".index("c"))
        # Beyond the BMP: four bytes, still one character.
        e = "😀x"
        print(len(e), ord(e[0]), e[1], e[0:1] == "😀")
    """,
    "builtin_protocol_is_reachable_by_name": """
        # `[].append` and `{}.keys` are lowered at the call site, which means
        # they exist as CALLS and never as attributes -- so `hasattr([1],
        # "__iter__")` answered False for the most iterable object in the
        # language, and every structural type test said no.
        print(hasattr([1], "__iter__"), hasattr([1], "__len__"))
        print(hasattr({}, "keys"), hasattr((1,), "index"))
        print([1, 2].__len__(), [1, 2].__contains__(2), [1, 2].__getitem__(0))
        print(sorted({"a": 1, "b": 2}.keys()))
        # SET TO None MEANS WITHDRAWN: `[].__hash__` is None rather than
        # missing, which is how a mutable container says it cannot be hashed.
        print([].__hash__, (1, 2).__hash__() == hash((1, 2)))
        # THE TYPE ANSWERS FOR ITS INSTANCES, and unbound -- `dict.keys` is
        # unbound in CPython too.
        print(hasattr(dict, "keys"), hasattr(list, "append"))

    """,
    "dict_views_print_as_views": """
        # A dict view rendered as the empty string: it had no repr of its own
        # and fell through to the default, so `print(d.keys())` printed
        # nothing at all.
        d = {"a": 1, "b": 2}
        print(d.keys())
        print(d.values())
        print(d.items())

    """,
    "typing_forms_print_as_written": """
        # THE UNION IS THE ONLY FORM THAT PRINTS WITH BARS. Testing "the
        # origin is an instance" made every parameterised form print as one,
        # so `Annotated[int, 'x']` came out as `int | 'x'`.
        from typing import Annotated, Literal, Optional, get_args

        print(Annotated, Literal)
        print(Annotated[int, "positive"])
        print(Literal["a", "b"])
        # `Optional[X]` IS `X | None` in 3.14, and `None` in a union is the
        # NoneType CLASS rather than the singleton.
        print(Optional[int], int | str)
        print(get_args(Optional[int]))
        print(get_args(int | None))

    """,
    "zero_argument_constructors": """
        # `list()` is `[]` -- the type's zero value, not a conversion of
        # nothing. Requiring exactly one argument rejected a program CPython
        # accepts, and `defaultdict(list)` is the one that hits it.
        print(int(), list(), tuple(), repr(str()), float())
        print(dict(), set(), repr(bytes()), bool(), frozenset())
        # AS A VALUE TOO: the thunk's parameter has to be optional, or the
        # call through it reports an arity error the direct call does not.
        make = list
        empty = dict
        print(make(), empty(), make([1, 2]))

    """,
    "module_def_can_be_rebound": """
        # A NAME DEFINED TWICE IS REBOUND, not an error: the second `def`
        # replaces it and the first stays reachable only through whatever
        # already held it. Refusing this rejected a program CPython runs --
        # `@f.register` over two `def _`s is the idiom that needs it.
        def f():
            return 1

        # Called BETWEEN the two, so it must reach the FIRST body. Resolving
        # the name where the call is compiled picked the survivor instead.
        print(f())

        def f():
            return 2

        print(f())

        def g():
            return "a"

        held = g

        def g():
            return "b"

        print(held(), g())

    """,
    "oserror_subsumes_the_old_names": """
        # PEP 3151: `IOError` and `EnvironmentError` ARE `OSError` -- the same
        # object, not subclasses -- and the errno decides which specific class
        # a two-argument construction builds.
        print(IOError is OSError, EnvironmentError is OSError)
        print(issubclass(FileNotFoundError, OSError),
              issubclass(BrokenPipeError, OSError))
        e = OSError(2, "No such file")
        # EVERY ARGUMENT IS KEPT: one was all the constructor took, so the
        # rest were dropped and `e.args` reported a one-element tuple.
        print(e.errno, e.strerror, e.args)
        print(type(e).__name__)
        # AND MORE THAN ONE PRINTS AS THE TUPLE, which is how CPython shows
        # `args` once there is more than one of them to show.
        two = ValueError("a", "b")
        print(str(two), repr(two), two.args)
        try:
            raise IOError("late")
        except OSError as caught:
            print("caught", caught)

    """,
    "dict_resized_while_walked": """
        # The table is rehashed by the write, so continuing the walk would
        # skip or repeat entries. CPython refuses rather than losing them.
        d = {"a": 1, "b": 2}
        try:
            for k in d:
                d["c"] = 3
        except RuntimeError:
            print("RuntimeError")
        print(len(d))
        # A LIST MAY CHANGE UNDER A WALK and CPython allows it -- the walk
        # simply sees the new length, so this must NOT refuse.
        xs = [1, 2, 3]
        seen = []
        for x in xs:
            seen.append(x)
            if len(xs) < 5:
                xs.append(x * 10)
        print(len(seen) > 3, xs[:5])

    """,
    "codecs_are_real_conversions": """
        # `encode` and `decode` IGNORED THE ENCODING ENTIRELY: text is held as
        # UTF-8, so the utf-8 spelling was right by accident and every other
        # one silently answered the internal bytes.
        s = "a\\u00e9\\u4e2d"
        for enc in ("utf-8", "utf-16", "utf-32"):
            b = s.encode(enc)
            print(enc, len(b), b.decode(enc) == s)
        try:
            s.encode("ascii")
        except UnicodeEncodeError:
            print("UnicodeEncodeError")
        # THE ERROR HANDLER DECIDES whether a bad byte is a refusal or a
        # replacement, so it has to reach the runtime rather than be dropped.
        print(ascii(s.encode("ascii", "replace")))
        raw = b"\\xff\\x61"
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            print("UnicodeDecodeError")
        print(ascii(raw.decode("utf-8", "replace")))
        print(ascii(raw.decode("utf-8", "ignore")))
        # EVERY BYTE IS A CODE POINT in latin-1, which is what makes it the
        # round trip for arbitrary octets.
        print(ascii(raw.decode("latin-1")), len(raw.decode("latin-1")))

    """,
    "module_objects_are_namespaces": """
        import types

        m = types.ModuleType("m")
        m.__getattr__ = lambda name: "dynamic:" + name
        print(m.__getattr__("anything"))
        # The CLASS is called `module`; `ModuleType` is the name it is
        # exported under and not the one the type carries.
        print(type(m).__name__)
        ns = types.SimpleNamespace(b=2, a=1)
        print(ns, ns.a + ns.b)

    """,
    "multiple_inheritance_and_the_mro": """
        # A second base used to be REFUSED, because every lookup walked a
        # straight chain of base pointers. C3 is the only order that keeps two
        # promises at once -- a class comes before its bases, and the bases
        # keep the order they were written in -- and no simple walk keeps both.
        class A:
            v = "A"

            def who(self):
                return "A"

        class B(A):
            v = "B"
            w = "Bw"

            def who(self):
                # `super()` HERE must reach C on a D instance and A on a B
                # one: only the RECEIVER's order knows what sits between.
                return "B" + super().who()

        class C(A):
            def who(self):
                return "C" + super().who()

        class D(B, C):
            def who(self):
                return "D" + super().who()

        print(D().who())
        print([k.__name__ for k in D.__mro__])
        print([k.__name__ for k in B.__mro__])

        # TWO UNRELATED BASES: the FIRST one wins for a name both define,
        # which is what "the written order is binding" means.
        class Mix:
            v = "Mix"
            m = "Mm"

        class Left(A, Mix):
            pass

        class Right(Mix, A):
            pass

        print(Left.v, Left.m, Right.v)
        print([k.__name__ for k in Left.__mro__])
        print([k.__name__ for k in Left.__bases__])
        print(isinstance(D(), A), isinstance(D(), C), issubclass(D, B))

    """,
    "mro_conflict_is_rejected": """
        # No order satisfies both promises, so there is no class to make.
        class A:
            pass

        class B(A):
            pass

        try:
            class Bad(A, B):
                pass
        except TypeError:
            print("TypeError")

        class Ok(B, A):
            pass

        print([k.__name__ for k in Ok.__mro__])

    """,
    "yield_from_steps_the_inner_generator": """
        # Delegation DRAINED the inner generator, so a value sent to the outer
        # one arrived after the inner had already run past every `yield` that
        # could read it -- and an infinite inner generator never started.
        def inner():
            got = yield "inner-1"
            yield ("inner-got", got)
            return "inner-return"

        def outer():
            result = yield from inner()
            yield ("outer-saw", result)

        g = outer()
        print(next(g))
        print(g.send("x"))
        print(next(g))

        # AND A NON-GENERATOR STILL WORKS: it has nowhere to put the sent
        # value and is simply advanced.
        def over(xs):
            yield from xs

        print(list(over([1, 2, 3])))

    """,
    "signature_is_recoverable_from_code": """
        # `co_varnames` left `*args` and `**kw` out entirely, and the defaults
        # were split on the COUNT OF KEYWORD-ONLY PARAMETERS -- so `def f(a,
        # b=1, *args, c)` reported `b`'s default as `c`'s.
        import inspect

        def f(a, b=1, *args, c, **kw):
            pass

        print(f.__defaults__, f.__kwdefaults__)
        code = f.__code__
        print(code.co_varnames, code.co_argcount, code.co_kwonlyargcount)
        sig = inspect.signature(f)
        print(str(sig))
        print(list(sig.parameters))
        print(sig.parameters["b"].default, sig.parameters["c"].kind.name)

    """,
    "pep695_type_parameters": """
        # PEP 695. A type alias is a NAME plus what it stands for; the type
        # parameters are in scope for the value and readable off the thing
        # they belong to.
        type Alias = list[int]

        def first[T](xs: list[T]) -> T:
            return xs[0]

        class Box[T]:
            def __init__(self, v: T):
                self.v = v

        print(Alias.__name__, Alias.__value__, type(Alias).__name__)
        print(first([1, 2]), Box("x").v)
        print(first.__type_params__[0].__name__)

    """,
    "unicode_predicates_are_exact": """
        # The predicates walked BYTES, so a multi-byte character was asked
        # about its own continuation bytes -- which belong to no class -- and
        # every non-ASCII string answered False. And `isdecimal`, `isdigit`
        # and `isnumeric` shared one test, which they are not.
        for s in ("abc123", "123", "Abc", "\u00b2", "\u2167", "\u00e9"):
            print(s.isalnum(), s.isalpha(), s.isdigit(), s.isdecimal(),
                  s.isnumeric(), s.isidentifier())
        print("\u03bb".isidentifier(), "caf\u00e9".isidentifier())
        print("1\u03bb".isidentifier(), "_\u4e2d".isidentifier())
        print("\u00c9".isupper(), "\u00e9".islower(), "\u00c9\u00e9".istitle())

    """,
    "range_is_a_lazy_sequence": """
        # `range` was MATERIALISED into a list, so `type(range(3)).__name__`
        # said `list` and `range(10**12)` would have built a trillion
        # elements. It is three numbers now, and every question about one is
        # arithmetic on them.
        r = range(0, 10, 2)
        print(r.start, r.stop, r.step, len(r))
        print(r[2], r[-1], r.index(4), r.count(4), r.count(5))
        print(list(r[1:3]), list(r[::-1]))
        print(range(3) == range(3), range(0, 3, 1) == range(3),
              range(3) == range(4))
        print(type(r).__name__, repr(range(3)), repr(r))
        big = range(10 ** 12)
        print(len(big), 10 ** 11 in big, -1 in big)
        print(list(range(5)), list(range(10, 0, -3)), sum(range(5)))
        print(sorted(range(3), reverse=True), list(reversed(range(4))))
        print(min(range(2, 9)), max(range(2, 9)), tuple(range(3)))
        for v in range(3):
            print("v", v)

    """,
    "a_base_need_not_be_a_class": """
        # PEP 560: `class C(Fake())` asks the object for `__mro_entries__` and
        # inherits whatever it answers. A base had to be a plain NAME, so
        # every library that builds one at run time was refused.
        class Base:
            def who(self):
                return "Base"

        class Fake:
            def __mro_entries__(self, bases):
                return (Base,)

        class C(Fake()):
            pass

        print([k.__name__ for k in C.__mro__])
        print(C().who())
        # `__orig_bases__` is what the statement ACTUALLY SAID, which the
        # resolved base has lost.
        print(C.__orig_bases__[0].__class__.__name__)

    """,
    "a_class_may_extend_a_builtin": """
        # `class D(dict)` was refused: a base had to be a class the module
        # defines. An instance of one now carries a real dict, and everything
        # the body does not write is answered from it.
        class WithMissing(dict):
            def __missing__(self, k):
                return "default:" + k

        w = WithMissing()
        print(w["nope"], len(w), isinstance(w, dict), isinstance(w, object))
        w["a"] = 1
        print(w["a"], len(w))
        del w["a"]
        print(len(w), isinstance({}, WithMissing))

        # `del obj[k]` IS `obj.__delitem__(k)` -- never dispatched before, so
        # a class that wrote one had it ignored.
        class Store:
            def __init__(self):
                self.d = {}

            def __setitem__(self, k, v):
                self.d[k] = v

            def __delitem__(self, k):
                del self.d[k]

            def __len__(self):
                return len(self.d)

        s = Store()
        s["x"] = 1
        print(len(s))
        del s["x"]
        print(len(s))

    """,
    "metaclass_is_inherited": """
        # A class with no `metaclass=` still has one when its BASE does, and
        # that cannot be decided where the class is compiled -- it is a
        # property of a run-time value. The lowering used to pick between two
        # shapes statically and so a subclass of a metaclassed base was built
        # as a plain type, silently losing every hook.
        class Meta(type):
            def __call__(cls, *a, **kw):
                print("through", cls.__name__)
                # `type.__call__` is the ordinary instantiation. Reaching it
                # is the only way this hook can end without calling itself.
                return super().__call__(*a, **kw)

            def tag(cls):
                return "tag:" + cls.__name__

        class Base(metaclass=Meta):
            def __init__(self, x):
                self.x = x

        class Sub(Base):
            pass

        print(type(Base).__name__, type(Sub).__name__)
        print(Sub(7).x)
        # A method the METACLASS defines is reached through the class, bound
        # to it -- the same relationship an instance has to its class.
        print(Base.tag(), Sub.tag())

    """,
    "metaclass_takes_class_keywords": """
        # `class C(metaclass=M, kind="x")` is `M(name, bases, ns, kind="x")`,
        # and the keywords have to be matched against `M.__new__` -- which is
        # where they are declared, `__init__` being `type`'s default.
        class M(type):
            def __new__(mcls, name, bases, ns, kind=None):
                cls = super().__new__(mcls, name, bases, ns)
                cls.kind = kind
                return cls

            def __iter__(cls):
                # Iterating a CLASS is the metaclass's business.
                return iter(cls.members)

        class C(metaclass=M, kind="x"):
            members = [1, 2, 3]

        print(C.kind, list(C), [n * 2 for n in C])

    """,
    "class_name_is_writable": """
        # `C.__name__ = ...` changes what the class is called. The name is a
        # field on the type rather than an entry in its dict, so storing it as
        # an ordinary attribute left `__name__` reading the old one -- a write
        # that appeared to succeed and changed nothing.
        class C:
            pass

        C.__name__ = "Renamed"
        # `__name__` only: CPython's class repr is built from `__qualname__`
        # and the module, neither of which this write touches, and the module
        # half is a divergence of its own.
        print(C.__name__)

        def f():
            pass

        f.__name__ = "g"
        print(f.__name__)

    """,
    "property_is_a_named_descriptor": """
        # `hasattr(p, "__get__")` is how a program asks whether something is a
        # descriptor. The behaviour existed with nothing naming it, so a
        # property answered False and read as an ordinary attribute.
        class C:
            def __init__(self):
                self._v = 1

            @property
            def v(self):
                return self._v

            @v.setter
            def v(self, new):
                self._v = new

        p = C.__dict__["v"]
        print(hasattr(p, "__get__"), hasattr(p, "__set__"))
        obj = C()
        print(p.__get__(obj, C))
        p.__set__(obj, 9)
        print(obj.v)

    """,
    "object_and_type_are_values": """
        # `object.__new__(cls)` is what a metaclass calls to build an instance
        # without running the class's own `__new__`, and neither `object` nor
        # `type` is a kind the way `int` is -- both are class objects the
        # moment a program names one.
        class C:
            def __init__(self):
                raise AssertionError("__init__ must not run")

        made = object.__new__(C)
        print(type(made).__name__, isinstance(made, C))
        print(object.__name__, type.__name__)

        class Meta(type):
            pass

        print(Meta.__base__ is type)

    """,
    "init_subclass_keywords": """
        # `class A(Base, tag="a")` is how a program CONFIGURES the hook, and
        # the keywords were dropped -- so every subclass looked identically
        # unconfigured, which is a wrong answer rather than a refusal. They
        # have to be matched by NAME against the hook's parameters, not handed
        # over as `**kw`.
        seen = []

        class Base:
            def __init_subclass__(cls, tag=None, **kw):
                seen.append((cls.__name__, tag, sorted(kw.items())))
                # `object.__init_subclass__` EXISTS and is the no-op that
                # terminates the chain; it had no value naming it.
                super().__init_subclass__(**kw)

        class A(Base, tag="a"):
            pass

        class B(Base):
            pass

        # NOT a class with an unconsumed keyword: `object.__init_subclass__`
        # takes none, so CPython raises there too and the reference would
        # disagree with itself.
        class C(Base, tag="c"):
            pass

        print(seen)
    """,
    "descriptors_learn_their_names": """
        # PEP 487: a descriptor is TOLD ITS OWN NAME after the class body is
        # complete. It cannot know it otherwise -- the expression that built
        # it had no idea what it was about to be assigned to.
        class Field:
            def __set_name__(self, owner, name):
                self.name = name
                self.owner = owner.__name__
            def __get__(self, obj, objtype=None):
                return "field:" + self.name + "@" + self.owner

        class C:
            a = Field()
            b = Field()

        print(C().a, C().b)

        # `@v.deleter` -- the third of the three. `del obj.v` had a slot to
        # read and no way to fill it, so it looked in the instance dict, found
        # nothing (a property never puts anything there) and reported a
        # missing attribute for one the class plainly defines.
        class P:
            def __init__(self):
                self._v = 1
            @property
            def v(self):
                return self._v
            @v.setter
            def v(self, n):
                self._v = n
            @v.deleter
            def v(self):
                self._v = "deleted"

        p = P()
        print(p.v)
        p.v = 5
        print(p.v)
        del p.v
        print(p.v, type(P.v).__name__)
    """,
    "type_is_an_object": """
        # `type(x)` answers a TYPE OBJECT, not its name. The name was a str,
        # so `type(a) is type(b)` compared two separately-built strings and was
        # False for two ints -- and `print(type(x))` said `int` where CPython
        # says `<class 'int'>`.
        #
        # `type(1) is int` holds because the frontend registers the canonical
        # thunk for every builtin type the module names at the TOP OF THE
        # ENTRY, before any statement. Registering lazily made the answer
        # depend on which side was evaluated first, which is why an earlier
        # attempt at this was reverted.
        print(type(1) is int, type("a") is str, type(1.5) is float)
        print(type([]) is list, type({}) is dict, type(1) is str)
        print(type(1), type("a"), type([]), type(1).__name__)
        small, big = 1, 10 ** 30
        print(type(small) is type(big), type(True) is bool)
        print(isinstance(1, type(2)), isinstance(1, object))
        print(issubclass(bool, int), issubclass(int, int), issubclass(int, str))

        class C:
            pass

        print(type(C()) is C, type(C).__name__, type(None).__name__)
        # STILL CALLABLE, which is the whole reason it is a thunk.
        print(int("7") + 1, list(map(int, ["1", "2"])), str(9) + "!")
    """,
    "a_builtin_type_is_one_object": """
        # `int` mentioned twice built two thunks, so `int == int` was False
        # and a set of types compared unequal to itself. Interning by NAME has
        # no evaluation order to depend on -- unlike the registry tried and
        # reverted for `type(1) is int`, which made the answer depend on which
        # of `type(1)` and `int` the program reached first.
        print(int is int, int == int, int is str, str == str)
        print({int, str} == {str, int}, [int] == [int], {int: 1}[int])
        # STILL CALLABLE, which is the whole reason it is a thunk.
        print(list(map(int, ["1", "2"])), int("7") + 1, isinstance(1, int))
        print(int, str, bool, float, list, dict)
        # A BUILTIN REACHED AS A VALUE is not a plain function:
        # `type(print).__name__` is `builtin_function_or_method`. A
        # synthesised thunk is an ordinary compiled function without the flag.
        def written():
            pass

        print(type(print).__name__, type(len).__name__)
        print(type(written).__name__, type(int).__name__)
    """,
    "typing_introspection": """
        # `get_origin(Literal["a"]) is Literal` is only True if the two
        # mentions of `Literal` are ONE object -- forms are interned by name
        # for the same reason the suspension token and `NotImplemented` are.
        from typing import Literal, TypeGuard, get_args, get_origin
        L = Literal["a", "b"]
        print(get_args(L), get_origin(L) is Literal, get_origin(L) is L)
        print(get_args(TypeGuard[int]), get_origin(list[int]))
        print(get_args(list[int]), get_args(dict[str, int]))
        # Anything that is not a parameterised type answers None and ().
        print(get_origin(42), get_args(42), get_origin(list), get_args("a"))
        # A UNION IS NOT A GENERIC ALIAS to a program that asks -- the name is
        # how it tells the two apart.
        print(type(int | str).__name__)
    """,
    "containers_render_their_elements": """
        # A container shows its elements with REPR, whichever of str/repr was
        # asked of the container -- and every element has to go through the
        # same renderer the element alone would. The interpreter handed the
        # job to Python's `repr`, which printed an ADDRESS for anything the
        # runtime defines and raised out of the bridge for a user instance.
        class P:
            def __init__(self, n):
                self.n = n
            def __repr__(self):
                return "P(" + str(self.n) + ")"

        print(P(1), [P(1), P(2)], (P(3),))
        print({"k": P(4)}, [KeyError("k")])
        print([int], (int,), {"t": str})
        print([], (), {}, set(), frozenset(), frozenset([1]))
        print((1,), (1, 2), ["a"], {"a": "b"})
        # A container that holds itself prints the ellipsis rather than
        # recurring -- which Python's repr was doing for us.
        xs = [1]
        xs.append(xs)
        print(xs)
    """,
    "locals_and_globals": """
        # PEP 667: an INDEPENDENT SNAPSHOT. Writing to the dict must not reach
        # the local, and assigning the local afterwards must not show up in
        # the dict -- both fall out of it being an ordinary dict built at the
        # call site, which is the only place the name-to-register mapping
        # still exists.
        g = "global"

        def f():
            x = 1
            snapshot = locals()
            x = 2
            snapshot["x"] = 99
            return snapshot.get("x"), locals().get("x"), x

        print(f())

        # A local the branch did not bind is ABSENT, not present as None --
        # and no ordinary read reaches it, so nothing but `locals()` itself
        # can be what decides its register needs the null it starts from.
        def maybe(flag):
            if flag:
                v = 1
            return locals().get("v", "unbound"), sorted(locals())

        print(maybe(True))
        print(maybe(False))
        print("g" in globals(), "f" in globals(), "nosuch" in globals())

        # `dir()` WITH NO ARGUMENT is the names in scope, sorted -- which is
        # `sorted(locals())`, and now that `locals()` exists there is nothing
        # else to build.
        def scoped():
            a = 1
            b = 2
            return dir()

        print(scoped(), "g" in dir(), "nosuch" in dir())

        def reads_the_global():
            return sorted(k for k in globals() if not k.startswith("_"))

        print(reads_the_global()[:2])
        # NOT `__builtins__` here. It is a module when a script runs and a
        # plain dict under `exec`, which is how this corpus runs CPython --
        # so the reference would disagree with itself, not with us.
        # `scoping/locals-and-globals-builtins` covers it as a real script.
    """,
    "mutable_buffers": """
        # A bytearray is the bytes kind with the buffer writable, and a
        # memoryview is a window on someone else's -- so what this pins is
        # that a write is SEEN through the other name, and that `bytes()` of
        # either is a snapshot that then stops changing.
        ba = bytearray(b"abcd")
        mv = memoryview(ba)
        frozen = bytes(mv)
        mv[0] = 122
        print(ba, frozen, bytes(mv))
        print(bytes(mv[1:3]), bytes(mv[::-1]))
        print(len(mv), mv.readonly, mv.nbytes, mv.itemsize, mv.format)
        print(memoryview(b"xy").readonly, bytes(bytearray(3)))
        # Slicing a bytearray gives a bytearray; adding to one keeps the
        # LEFT operand's kind, which is what CPython does.
        print(ba[1:3], ba + b"e", b"e" + ba)
        print(ba == bytearray(b"zbcd"), ba == b"zbcd", type(ba).__name__)
        try:
            {ba: 1}
        except TypeError:
            print("unhashable")
        try:
            memoryview(b"xy")[0] = 1
        except TypeError:
            print("read-only")
        # BY CONTENT, not by buffer address. Two identical literals share one
        # static buffer in the compiled program, so comparing those two would
        # have passed while a bytes value BUILT at run time compared unequal
        # to the literal it matches -- and every dict lookup and `in` test on
        # a bytes key went the same way.
        built = b"a" + b"b"
        print(built == b"ab", built != b"ab", b"ab" == b"ac")
        print(b"ab" in [b"a" + b"b"], {b"ab": 1}[built])
    """,
    "dict_views_are_live": """
        # A VIEW IS A WINDOW ON THE DICT, not a copy. A snapshot is the
        # obvious implementation and is wrong exactly when a program relies on
        # the view being live -- after the dict changes.
        d = {'a': 1}
        ks = d.keys()
        print(sorted(ks))
        d['b'] = 2
        print(sorted(ks), len(ks))
        print(sorted(d.values()), sorted(d.items()))
        print('a' in d.keys(), 'z' in d.keys(), list(d.keys()))
        for k in d.keys():
            print('k', k)
        # A view is SET-LIKE: `&`, `|`, `-` all work against a real set.
        print(sorted(d.keys() & {'a'}), sorted(d.keys() | {'c'}))
        print(sorted(d.keys() - {'a'}))
    """,
    "float_hex": """
        # `hex` REACHES BYTES OR A FLOAT, and the two answer entirely
        # different things -- so the no-argument form dispatches on the
        # receiver, as `pop`, `split` and `count` already do.
        print((2.5).hex(), (0.0).hex(), (-0.0).hex())
        print((1.0).hex(), (0.1).hex(), float.fromhex('0x1.4p+1'))
        print(bytes([1, 255]).hex())
        print((2.5).is_integer(), (2.0).is_integer(), (2.5).as_integer_ratio())
    """,
    "dunders_on_builtins": """
        # DUNDERS CALLED DIRECTLY ON A BUILTIN are ordinary Python, and each
        # is the operation the runtime already performs for the operator form
        # -- the same symbol reached by another spelling, not a second
        # implementation that could disagree with it.
        print((-5).__abs__(), (0.0).__bool__(), (1.5).__trunc__())
        print((3).__neg__(), [1, 2].__len__(), (5).__repr__(), 'a'.__str__())
        # EVERY NUMBER HAS `real` AND `imag`, not only a complex one. An int's
        # imaginary part is the INT zero, which `type()` can tell apart.
        print((7).conjugate(), (7).real, (7).imag)
        print((2.5).real, (2.5).imag, type((7).imag).__name__)
    """,
    "ascii_escapes_what_repr_keeps": """
        # `ascii(x)` IS `repr(x)` WITH NOTHING ABOVE 0x7F LEFT IN IT. The two
        # agree for every ASCII value, which is why `!a` could be folded into
        # `!r` for a long time without anything noticing.
        #
        # THE PERMISSIVE UTF-8 STEP, not the validating one: `ascii` names
        # whatever byte is there rather than refusing it, because dropping a
        # bad byte or emitting it raw are both worse than `ÿ`.
        for s in ("abc", "café", "中文", "😀",
                  " ", "it's", "tab	here"):
            print(ascii(s))
        print(ascii([1, "café"]), ascii({"é": "ü"}))
        print(ascii(5), ascii(None), ascii(b"ab"))
        # AND THE THREE PLACES A CONVERSION IS SPELLED, which reach it by
        # three different routes: an f-string is lowered by the frontend,
        # `str.format` parses its own replacement fields, and `%a` goes
        # through the percent formatter.
        print(f"{'café'!a}", "{!a}".format("café"), "%r" % "café")
        print(f"{'é'!a:>10}|{None!a}")
    """,
    "oserror_shows_its_errno": """
        # `str()` OF THE OSError FAMILY IS NOT ITS ARGUMENTS. CPython puts
        # `[Errno n] message` on the two-argument form and appends the quoted
        # filename on the three -- and the whole family arrives under its own
        # name, so this is a walk up the hierarchy and not a test for
        # `OSError` itself. `repr` is unaffected and still shows the call.
        #
        # THE FILENAME IS QUOTED AND THE MESSAGE IS NOT, which reads as an
        # inconsistency and is deliberate: the message is prose, and a path
        # with a trailing space is invisible unrendered.
        for e in (OSError(2, "No such file"),
                  OSError(2, "No such file", "f.txt"),
                  PermissionError(13, "Permission denied"),
                  OSError("plain"),
                  OSError()):
            print(str(e))
        print(str(ValueError(1, "not an oserror")))
    """,
    "self_referential_repr": """
        # A CONTAINER THAT HOLDS ITSELF is an ordinary thing to build, and
        # rendering it naively recurses until the stack runs out. Python
        # prints the repeat as `[...]`, which is what makes it finite.
        xs = [1]
        xs.append(xs)
        print(xs, len(xs), xs[1] is xs)
        d = {'a': 1}
        d['self'] = d
        print(d)
        t = ([1],)
        t[0].append(t)
        print(t)
        # Nested but NOT cyclic still renders in full -- the check is about
        # re-entry, not about depth.
        print([[1, 2], [3]], {'x': {'y': 1}})
    """,
    "raise_a_variable": """
        # `raise e` WHERE `e` IS A VARIABLE re-raises the object it holds.
        # Only a name that IS an exception type means "make one of these" --
        # treating every name that way built an exception named after the
        # variable, so the handler for the real type never fired.
        e = ValueError('v')
        try:
            raise e
        except ValueError as x:
            print('caught', x, type(x).__name__)
        for exc in (ValueError('v'), TypeError('t'), KeyError('k'),
                    IndexError('i')):
            try:
                raise exc
            except LookupError as x:
                print('LookupError', type(x).__name__)
            except (ValueError, TypeError) as x:
                print('ValueError-or-TypeError', type(x).__name__)
        print(issubclass(KeyError, LookupError),
              issubclass(ZeroDivisionError, ArithmeticError))
        # `sum` REFUSES STRINGS -- the concatenation works, which is exactly
        # why the refusal has to be explicit.
        print(sum([1, 2, 3]), sum([1.0], 0.0), sum([True, True, False]))
        try:
            sum(['a', 'b'], '')
        except TypeError as x:
            print('TypeError:', x)
    """,
    "notimplemented_falls_back": """
        # `NotImplemented` MEANS "ASK THE OTHER OPERAND", not "the answer is
        # NotImplemented". Returning it as the result made the comparison
        # answer the sentinel instead of falling back.
        class Left:
            def __eq__(self, other):
                return NotImplemented
        class Right:
            def __eq__(self, other):
                return 'right-wins'
        print(Left() == Right())
        # Neither side answers: the default is IDENTITY for `==`.
        print(Left() == Left())
        class A:
            def __add__(self, o):
                return NotImplemented
        class B:
            def __radd__(self, o):
                return 'B-wins'
        print(A() + B())
        # Neither side answers for arithmetic: a TypeError naming the pair.
        try:
            A() + A()
        except TypeError as e:
            print('TypeError:', e)
        print(NotImplemented is NotImplemented, str(NotImplemented))
    """,
    "numeric_conversion_dunders": """
        # A CLASS SAYS WHAT ITS NUMBER IS. Answering from the numeric tower
        # instead converted something the class never claimed was a number.
        class N:
            def __int__(self):
                return 7
            def __float__(self):
                return 7.5
            def __complex__(self):
                return complex(1, 2)
            def __round__(self, nd=None):
                return 'round:' + str(nd)
            def __bool__(self):
                return False
        n = N()
        print(int(n), float(n), complex(n))
        print(round(n), round(n, 2))
        print(bool(n))
        # `__index__` stands in for both when the class defines only it.
        class I:
            def __index__(self):
                return 3
        print(int(I()), float(I()))
        # `complex(x)` asks the class; `complex(x, 0)` is building from parts
        # and has nothing to ask -- so "omitted" and "given as 0" are not the
        # same thing.
        print(complex(), complex(1), complex(1, 2), complex(1.5, -2))
        print(round(2.5), round(2.567, 2), round(7))
    """,
    "iteration_protocol_hooks": """
        # `__reversed__` WINS OVER THE INDEX WALK. A class may define both it
        # and `__getitem__`, and they need not agree -- walking indices
        # backwards instead silently produced a different sequence.
        class C:
            def __reversed__(self):
                return iter(['z', 'y'])
            def __len__(self):
                return 2
            def __getitem__(self, i):
                return 'ab'[i]
        print(list(reversed(C())))
        class Seq:
            def __len__(self):
                return 3
            def __getitem__(self, i):
                return i * 10
        print(list(reversed(Seq())))
        # WHAT `__iter__` RETURNS MUST BE AN ITERATOR. A str is not one however
        # walkable it looks; accepting it turned a broken class into a working
        # one that iterated something else entirely.
        class BadIter:
            def __iter__(self):
                return 'not-an-iterator'
        try:
            list(BadIter())
        except TypeError as e:
            print('TypeError:', e)
        class GoodIter:
            def __iter__(self):
                return iter([1, 2])
        print(list(GoodIter()))
        # A SET HAS NO ORDER TO REVERSE, and it has a length -- so the index
        # walk would have answered confidently.
        try:
            reversed({1, 2})
        except TypeError as e:
            print('TypeError:', e)
        print(list(reversed([1, 2, 3])), list(reversed('abc')))
    """,
    "ascii_escapes": """
        # `ascii` IS NOT `repr`. Its answer has to survive a channel that
        # cannot carry the character, so handing back the character defeats
        # the whole point of it.
        s = 'a' + chr(233)
        print(repr(s))
        print(ascii(s))
        print(ascii([s]))
        print(ascii('plain'), ascii(1), ascii(b'a'))
        print(ascii(chr(0x4e2d)))
    """,
    "sys_module": """
        import sys
        # ONLY WHAT CAN BE ANSWERED HONESTLY: most of `sys` describes a running
        # interpreter that is not there. What it does know is which
        # implementation compiled the program.
        print(type(sys.implementation.name).__name__)
        print(len(sys.implementation.version) >= 3)
        print(sys.implementation.name == sys.implementation.name.lower())
        print(isinstance(sys.implementation.hexversion, int))
        print(sys.byteorder, sys.maxsize > 0)
    """,
    "builtin_types_as_values": """
        # A BUILTIN TYPE NAME IS BOTH. As a value it is a callable -- which is
        # what `map(str, xs)` needs -- and it is also a class, which is what
        # `print(int)` and `isinstance(x, t)` ask about. Answering
        # `<function int at 0x...>` to the second was a wrong answer.
        print(int, str, list, dict, bool)
        print(list[int], dict[str, int], tuple[int, str])
        t = int
        print(isinstance(1, t), isinstance(1.0, t), isinstance(True, t))
        print(list(map(str, [1, 2])), sorted([3, 1], key=int))
        print(int('42'), str(9), list((1, 2)), dict([('a', 1)]))
        print(type(1).__name__)
        class C:
            def __class_getitem__(cls, item):
                return str(cls.__name__) + '[' + str(item) + ']'
        print(C[int])
        # A class WITHOUT the hook is not subscriptable, as CPython says.
        class D:
            pass
        try:
            D[int]
        except TypeError as e:
            print('TypeError:', e)
    """,
    "init_subclass_hook": """
        # ANNOUNCED TO THE BASE, not to the class itself, and after the body
        # has been filled -- the hook routinely reads what the body bound.
        seen = []
        class Base:
            def __init_subclass__(cls, **kw):
                seen.append(cls.__name__)
        class A(Base):
            pass
        class B(Base):
            pass
        class C(A):
            pass
        print(seen)
        names = []
        class Reg:
            def __init_subclass__(cls, **kw):
                names.append((cls.__name__, getattr(cls, 'tag', None)))
        class R1(Reg):
            tag = 'one'
        class R2(Reg):
            tag = 'two'
        print(names)
    """,
    "exception_groups": """
        # PEP 654. A group IS an exception -- it propagates the same way -- and
        # what distinguishes it is the exceptions it carries.
        eg = ExceptionGroup('outer', [ValueError('v1'),
                                      ExceptionGroup('inner', [TypeError('t1')])])
        print(type(eg).__name__, len(eg.exceptions))
        # `split` PRESERVES THE NESTING: a match inside an inner group comes
        # back inside an inner group, so the two halves add up to the original.
        m, rest = eg.split(ValueError)
        print(type(m).__name__, len(m.exceptions))
        print(type(rest).__name__, len(rest.exceptions))
        print(eg.subgroup(TypeError) is not None, eg.subgroup(KeyError) is None)
        print(isinstance(eg, Exception), isinstance(eg, ExceptionGroup))
        try:
            raise ExceptionGroup('boom', [ValueError('a')])
        except ExceptionGroup as g:
            print('caught', len(g.exceptions))
        # An exception type reached AS A VALUE, which `split` relies on and
        # which used to answer False for every exception.
        t = ValueError
        print(isinstance(ValueError('x'), t), isinstance(ValueError('x'), KeyError))
        print('a b'.split(), 'a,b'.split(','))
    """,
    "dir_builtin": """
        class C:
            def __dir__(self):
                return ['b', 'a', 'a']
        # SORTED BUT NOT DEDUPLICATED: CPython sorts what the hook returned
        # and hands it back.
        print(dir(C()))
        class D:
            x = 1
            def m(self):
                pass
        d = D()
        d.own = 2
        names = dir(d)
        print('x' in names, 'm' in names, 'own' in names)
        class E(D):
            y = 3
        print('x' in dir(E()), 'y' in dir(E()))
        print(dir(D) == sorted(dir(D)))
    """,
    "async_context_manager": """
        import asyncio
        log = []
        class ACM:
            def __init__(self, swallow):
                self.swallow = swallow
            async def __aenter__(self):
                log.append('aenter')
                await asyncio.sleep(0)
                return 'value'
            async def __aexit__(self, et, ev, tb):
                log.append(('aexit', et.__name__ if et else None))
                await asyncio.sleep(0)
                return self.swallow
        async def main():
            async with ACM(False) as v:
                log.append(('body', v))
            # THE EXCEPTION PATH SUSPENDS: `await __aexit__(...)` returns from
            # the step function between computing the live exception and
            # re-raising it, so neither can live in a register. That is what
            # made the first version produce IR the verifier rejected.
            try:
                async with ACM(False):
                    raise ValueError('boom')
            except ValueError as e:
                log.append(('caught', str(e)))
            # A true return from `__aexit__` SWALLOWS rather than observes.
            async with ACM(True):
                raise KeyError('swallowed')
            log.append('after')
            return log
        print(asyncio.run(main()))
    """,
    "slice_objects": """
        # A SLICE REACHES A USER `__getitem__` AS AN OBJECT -- the class
        # decides what a slice of it means, and it can only do that if it is
        # handed one. Slicing a list still goes straight through without
        # allocating; only these paths build the object.
        class C:
            def __getitem__(self, key):
                if isinstance(key, slice):
                    return ('slice', key.start, key.stop, key.step)
                return ('index', key)
        c = C()
        print(c[1])
        print(c[1:2])
        print(c[::2])
        print(c[1:2, 3])
        print(slice(5), slice(1, 5), slice(1, 10, 2))
        s = slice(1, 10, 2)
        print(s.start, s.stop, s.step)
        print([0, 1, 2, 3, 4, 5][s])
        print('abcdef'[slice(2, 4)])
        print([1, 2, 3, 4][slice(None, None, -1)])
        print(slice(1, 5, 2).indices(10), slice(None, None, -1).indices(5))
        print(slice(-3, None).indices(10))
        # Assigning through a slice may CHANGE THE LENGTH, and does it in
        # place so every other name bound to the list sees it.
        xs = [0, 1, 2, 3]
        xs[1:3] = [9]
        print(xs)
        ys = [0, 1, 2, 3, 4]
        alias = ys
        ys[:] = [7, 8]
        print(ys, alias)
        zs = [1, 2, 3]
        zs[1:1] = [9, 9]
        print(zs)
    """,
    "match_statement": """
        class Point:
            __match_args__ = ('x', 'y')
            def __init__(self, x, y):
                self.x, self.y = x, y
        def f(v):
            match v:
                case []:
                    return 'empty'
                case [1, *rest]:
                    return 'one-then:' + str(rest)
                case [a, b] if a == b:
                    return 'pair-equal:' + str(a)
                case [a, *mid, b]:
                    return 'ends:' + str(a) + ',' + str(b) + ' mid=' + str(mid)
                case {'t': 'a', 'v': val}:
                    return 'tagged-a:' + str(val)
                case {'t': t, **rest}:
                    return 'tagged:' + str(t) + ' rest=' + str(sorted(rest))
                case Point(0, 0):
                    return 'origin'
                case Point(x=0, y=y):
                    return 'on-y:' + str(y)
                case Point(px, py):
                    return 'point:' + str(px) + ',' + str(py)
                case str(s):
                    return 'str:' + s
                case int(n) if n > 100:
                    return 'big:' + str(n)
                # `True` reaches this and NOT the `case True` below, because
                # bool is an int subclass -- the ordering is observable.
                case (int() | float()) as num:
                    return 'num:' + str(num)
                case None:
                    return 'none'
                case True:
                    return 'true'
                case other:
                    return 'other:' + str(other)
        for v in ([], [1, 2, 3], [4, 4], [1], [7, 8, 9], {'t': 'a', 'v': 5},
                  {'t': 'b', 'z': 1}, Point(0, 0), Point(0, 7), Point(3, 4),
                  'hi', 500, 3.5, None, True):
            print(f(v))
        def g(v):
            match v:
                case [[a, b], [c, d]]:
                    return a + b + c + d
                case _:
                    return -1
        print(g([[1, 2], [3, 4]]), g([1, 2]))
        # A `match` with nothing matching does nothing -- `case _` is optional.
        def h(v):
            out = 'untouched'
            match v:
                case 99:
                    out = 'ninetynine'
            return out
        print(h(99), h(1))
        # A str is NOT a sequence pattern: this must fall through.
        def s(v):
            match v:
                case [x, y]:
                    return 'seq:' + str(x) + str(y)
                case _:
                    return 'not-a-sequence'
        print(s('ab'), s([1, 2]))
    """,
    "reraise_runs_finally": """
        log = []
        def f():
            try:
                raise ValueError('x')
            except ValueError:
                log.append('caught')
                # A BARE `raise` RE-RAISES WHAT THE HANDLER CAUGHT. Entering a
                # handler clears the error flag, so this used to propagate
                # nothing at all -- the exception vanished and the outer
                # `except` never fired.
                raise
            finally:
                # AND THE `finally` STILL RUNS on the way out. Handler bodies
                # are lowered with their own `try` already popped, so a raise
                # there jumped straight to the enclosing handler.
                log.append('finally')
        try:
            f()
        except ValueError:
            log.append('outer')
        print(log)
        def g():
            try:
                yield 1
            except GeneratorExit:
                log.append('exit')
                raise
            finally:
                log.append('gfinally')
        it = g()
        next(it)
        it.close()
        it.close()
        print(log[3:])
        # `else` and `finally` together, with a handler that never runs: the
        # rethrow path must still terminate its block.
        try:
            print('fine')
        except ValueError:
            print('not reached')
        else:
            print('else ran')
        finally:
            print('finally ran')
        def h():
            yield 1
            return 'done'
        it2 = h()
        print(next(it2))
        try:
            next(it2)
        except StopIteration as e:
            # `next()` carries the generator's return value out; `yield from`
            # read it correctly while this spelling answered None.
            print('StopIteration', e.value)
    """,
    "descriptors": """
        class Base:
            @property
            def v(self):
                return 'base'
        class Sub(Base):
            @property
            def v(self):
                # THROUGH THE CLASS a property is ITSELF, which is the only
                # way an override reaches the getter it is extending.
                return 'sub:' + Base.v.fget(self)
        print(Base().v, Sub().v)
        class P:
            def __init__(self):
                self._v = 1
            @property
            def v(self):
                return self._v * 10
            @v.setter
            def v(self, n):
                self._v = n + 1
        p = P()
        print(p.v)
        p.v = 4
        print(p.v)
        class C:
            tag = 'base'
            @classmethod
            def make(cls):
                return cls.tag
            @staticmethod
            def plain(n):
                return n * 2
        class D(C):
            tag = 'derived'
        print(C.make(), D.make(), C.plain(3))
        # A DATA descriptor beats the instance dict; a NON-data one loses to
        # it. That difference is the whole protocol.
        class Data:
            def __get__(self, obj, t=None):
                return 'data'
            def __set__(self, obj, v):
                obj.__dict__['v'] = v
        class NonData:
            def __get__(self, obj, t=None):
                return 'non-data'
        class Holder:
            v = Data()
            n = NonData()
        h = Holder()
        h.v = 1
        h.__dict__['n'] = 'instance'
        print(h.v, h.n, h.__dict__['v'])
        # A class body is a SCOPE: it runs top to bottom and reads what it
        # has already bound.
        class Scoped:
            x = 1
            y = x + 1
        print(Scoped.y)
    """,
    "pop_across_receivers": """
        # ONE METHOD NAME, THREE RECEIVERS. `xs.pop(i)` takes an index,
        # `d.pop(k)` takes a key, and `s.pop()` takes nothing -- and which one
        # is meant is not known until run time.
        xs = [1, 2, 3]
        print(xs.pop(), xs.pop(0), xs)
        s = {9}
        print(s.pop(), len(s))
        d = {'a': 1, 'b': 2}
        print(d.pop('a'), d.pop('zz', 'dflt'), sorted(d))
        try:
            d.pop('nope')
        except KeyError as e:
            print('KeyError', e)
        print(d.popitem(), len(d))
        try:
            {}.popitem()
        except KeyError as e:
            print('empty:', e)
        try:
            [].pop()
        except IndexError as e:
            print('IndexError:', e)
    """,
    "set_iteration_order": """
        # CPython holds a set in an open-addressed table, so `{3, 1, 2}`
        # iterates as 1, 2, 3 -- the three land in slots 3, 1 and 2 of an
        # eight-slot table whatever order they were written in. Insertion
        # order, which this used to produce, is the one thing CPython's order
        # is never about, and seven conformance cases read it back.
        s = {3, 1, 2}
        print(list(s), len(s), sorted(s))
        print(list(set(range(20)))[:10])
        print(list({10, 3, 7, 1}))
        print(sorted({'b', 'a'}))
        d = {1, 2, 3}
        d.add(4)
        d.add(0)
        d.discard(2)
        print(list(d))
        print(list({1, 2} | {3, 4}), list({1, 2, 3} & {2, 3, 4}))
        print(list(frozenset({5, 2, 9})))
        print({1, 2, 3} == {3, 2, 1}, 2 in d, 99 in d)
        e = set()
        for i in [5, 3, 8, 1]:
            e.add(i)
        print(list(e))
        print([v for v in {4, 2, 6}], tuple({7, 3}))
    """,
    "inspect_coroutine_questions": """
        import asyncio
        import inspect
        async def coro():
            return 1
        def gen():
            yield 1
        async def agen():
            yield 1
        c = coro()
        print(inspect.iscoroutine(c), inspect.isgenerator(c))
        print(inspect.isgenerator(gen()), inspect.iscoroutine(gen()))
        # An async generator is NEITHER, which is the distinction that makes
        # three flags rather than one.
        a = agen()
        print(inspect.iscoroutine(a), inspect.isgenerator(a),
              inspect.isasyncgen(a))
        print(inspect.iscoroutinefunction(coro),
              inspect.iscoroutinefunction(gen))
        print(asyncio.run(c))
    """,
    "async_generators": """
        import asyncio
        log = []
        async def agen():
            try:
                for i in range(5):
                    yield i
            finally:
                log.append('cleanup')
        async def main():
            out = []
            async for v in agen():
                out.append(v)
                if v == 1:
                    break
            return out
        print(asyncio.run(main()))
        # The `finally` runs when the loop closes what the program abandoned,
        # not when the `async for` is left -- `break` leaves the generator
        # suspended inside its own `try`.
        print(log)
        async def nums(n):
            for i in range(n):
                await asyncio.sleep(0)
                yield i
        async def forms():
            lst = [v async for v in nums(3)]
            st = {v async for v in nums(3)}
            dct = {v: v * 2 async for v in nums(2)}
            filt = [v async for v in nums(3) if v]
            return lst, sorted(st), sorted(dct.items()), filt
        print(asyncio.run(forms()))
        print(type(nums(1)).__name__)
    """,
    "coroutines_and_gather": """
        import asyncio
        log = []
        async def task(name, rounds):
            for i in range(rounds):
                log.append((name, i))
                await asyncio.sleep(0)
            return name
        async def main():
            return await asyncio.gather(task('a', 3), task('b', 2))
        print(asyncio.run(main()))
        # THE INTERLEAVING IS THE POINT. A drained `await` -- one that ran the
        # inner coroutine to completion instead of suspending -- prints the
        # same results list and a different log, and passes every conformance
        # case either way. This line is what tells the two apart.
        print(log)
        async def val(v):
            await asyncio.sleep(0)
            return v
        async def seq():
            a = await val(1)
            b = await val(2)
            return a + b
        print(asyncio.run(seq()))
        never = []
        async def unused():
            never.append('ran')
        c = unused()
        print(never, type(c).__name__)
        asyncio.run(c)
        print(never)
        # SLEEP DURATION DECIDES WAKE ORDER. A `sleep` that ignored its delay
        # completed these round-robin -- 'slow' first, because it was stepped
        # first -- and no conformance case noticed, since every one of them
        # sleeps for 0 and only checks `gather`'s results, which are ordered
        # by argument either way.
        order = []
        async def timed(name, delay):
            await asyncio.sleep(delay)
            order.append(name)
            return name
        async def race():
            return await asyncio.gather(timed('slow', 0.05), timed('fast', 0.001))
        print(asyncio.run(race()))
        print(order)
    """,
    "int_passed_to_a_float_parameter": """
        def scale(x: float) -> float:
            return x * 2.0
        def half(x: float) -> float:
            return x / 2.0
        print(scale(42), scale(3.5))
        print(half(7), half(7.0))
        print(scale(0), scale(-3))
        total = 0.0
        for i in range(4):
            total = total + scale(i)
        print(total)
    """,
    "format_mini_language_numbers": """
        print(format(42, '08.2f'))
        print(format(1234, 'e'), format(1234, '.2e'))
        print(format(0.5, '%'), format(0.5, '.1%'))
        print(format(1234, 'g'), format(0.000012345, 'g'))
        print(format(3.14159, '.3'), format(3.14159, '10.3f') + '|')
        print(format(255, 'c') == chr(255))
        print(format(255, '#x'), format(255, '#o'), format(255, '#b'))
        print(format(1234567, ','), format(-42, '=+8d'))
        print(format('ab', '*^8') + '|', format('abcdef', '.3'))
    """,
    "typing_is_inert": """
        from typing import Final, final, override, Optional, LiteralString
        MAX: Final = 10
        print(MAX)
        @final
        class Sealed:
            pass
        class Still(Sealed):
            pass
        print(Still.__name__, getattr(Sealed, '__final__', False))
        class Base:
            def m(self):
                return 'base'
        class Sub(Base):
            @override
            def m(self):
                return 'sub'
        print(Sub().m(), Sub.m.__override__)
        def q(s: LiteralString) -> str:
            return s
        print(q('ok'))
        print(Optional.__class__.__name__ != '')
    """,
    "string_translation": """
        table = str.maketrans('ab', 'xy')
        print('aabb'.translate(table))
        print('abc'.translate(str.maketrans('', '', 'b')))
        print('hello'.translate({ord('l'): 'L'}))
        print('abcabc'.count('a', 1), 'abc'.count(''), 'aaaa'.count('aa'))
        print(chr(223).upper(), chr(223).casefold())
        print('a\\tb'.expandtabs(4))
    """,
    "function_attributes": """
        def f():
            return 1
        f.tag = 'x'
        f.count = 3
        print(f.tag, f.count, f())
        print(getattr(f, 'tag'), getattr(f, 'nope', 'fallback'))
        print(hasattr(f, 'tag'), hasattr(f, 'nope'))
        def mark(fn):
            fn.marked = True
            return fn
        @mark
        def g():
            return 2
        print(g.marked, g(), g.__name__)
        class C:
            def m(self):
                return 3
        print(getattr(C, 'missing', 'none'))
    """,
    "hash_and_eq_contract": """
        class Point:
            def __init__(self, x):
                self.x = x
            def __eq__(self, o):
                return isinstance(o, Point) and self.x == o.x
            def __hash__(self):
                return hash(self.x)
        a, b = Point(1), Point(1)
        print(a == b, hash(a) == hash(b))
        print(len({a, b}))
        print({a: 'v'}[b])
        class OnlyEq:
            def __eq__(self, o):
                return True
        try:
            print(hash(OnlyEq()))
        except TypeError as e:
            print('TypeError:', e)
        class Plain:
            pass
        print(isinstance(hash(Plain()), int))
        # `__eq__` WITHOUT `__hash__` makes a class unhashable, and a CONTAINER
        # has to find that out too. `hash(x)` refused it already; `{x: 1}` did
        # not, and built a mapping whose key could never be looked up again --
        # a silent wrong answer where CPython raises.
        class OnlyEq2:
            def __eq__(self, o):
                return True
        print(OnlyEq2.__hash__)
        for make in ('dict', 'set'):
            try:
                if make == 'dict':
                    {OnlyEq2(): 1}
                else:
                    {OnlyEq2()}
            except TypeError as e:
                print(make, '->', e)
    """,
    "dunder_protocols": """
        class Odd:
            def __lt__(self, o):
                return 'lt'
            def __gt__(self, o):
                return 'gt'
            def __eq__(self, o):
                return 'eq'
        odd = Odd()
        print(odd < 1, odd > 1, odd == 1, 1 > odd, 1 < odd)
        class Unary:
            def __neg__(self):
                return 'neg'
            def __pos__(self):
                return 'pos'
            def __abs__(self):
                return 'abs'
        u = Unary()
        print(-u, +u, abs(u), -5, +5, abs(-5), ~5)
        class Two:
            def __index__(self):
                return 2
        print([10, 20, 30][Two()], 'abcd'[Two():], hex(Two()), bin(Two()))
        class Missing:
            def __init__(self):
                self.real = 1
            def __getattr__(self, name):
                return 'missing:' + name
        m = Missing()
        print(m.real, m.nope)
        class Private:
            def __init__(self):
                self.__hidden = 1
            def peek(self):
                return self.__hidden
            def __helper(self):
                return 'helped'
            def call_helper(self):
                return self.__helper()
        pv = Private()
        print(pv.peek(), pv._Private__hidden, hasattr(pv, '__hidden'))
        print(pv.call_helper(), sorted(vars(pv)))
        # THE RIGHT-HAND SIDE FIRST, which is the opposite of reading order.
        order = []
        def probe(n):
            order.append(n)
            return n
        class Sink:
            def __setitem__(self, k, v):
                order.append(('set', k, v))
        Sink()[probe('key')] = probe('value')
        print(order)
    """,
    "in_place_operators": """
        # `x += y` is NOT `x = x + y`: a list extends itself, so every other
        # name bound to it sees the change -- observable from another frame.
        def extend(xs):
            xs += [99]
        xs = [1]
        extend(xs)
        print(xs)
        def rebind(t):
            t += (99,)
        t = (1,)
        rebind(t)
        print(t)
        a = [1, 2]
        b = a
        a += [3]
        print(a, b, a is b)
        s1 = {1, 2}
        s2 = s1
        s1 |= {3}
        print(sorted(s1), sorted(s2), s1 is s2)
        s1 -= {1}
        print(sorted(s1))
        d = {'a': 1}
        e = d
        d |= {'b': 2}
        print(sorted(d.items()), d is e)
        n = 5
        n += 1
        st = 'a'
        st += 'b'
        print(n, st)
        row = [[0]]
        row[0] += [1]
        print(row)
        class Box:
            def __init__(self):
                self.v = [0]
        box = Box()
        box.v += [1]
        print(box.v)
    """,
    "finally_on_every_exit": """
        log = []
        for i in range(3):
            try:
                if i == 0:
                    continue
                if i == 2:
                    break
                log.append(('body', i))
            finally:
                log.append(('finally', i))
        print(log)
        def nested():
            out = []
            for i in range(3):
                try:
                    try:
                        if i == 1:
                            break
                        out.append(i)
                    finally:
                        out.append('inner')
                finally:
                    out.append('outer')
            return out
        print(nested())
        def wins():
            while True:
                try:
                    break
                finally:
                    return 'from-finally'
        print(wins())
        # The returned value is computed BEFORE the finally runs.
        def snapshot():
            n = 1
            try:
                return n
            finally:
                n = 99
        print(snapshot())
        marks = []
        def handler(mode):
            try:
                if mode == 'raise':
                    raise ValueError('x')
                return 'returned'
            except ValueError:
                return 'caught'
            finally:
                marks.append(mode)
        print(handler('ok'), handler('raise'), marks)
        try:
            try:
                raise ValueError('original')
            finally:
                raise KeyError('from-finally')
        except KeyError as e:
            print(type(e).__name__, type(e.__context__).__name__)
    """,
    "dicts_and_dunder_attributes": """
        a = {'x': 1, 'y': 2}
        b = {'y': 20, 'z': 30}
        print(sorted((a | b).items()), sorted((b | a).items()))
        c = dict(a)
        c |= b
        print(sorted(c.items()), sorted(a.items()))
        class Holder:
            shared = 1
            def __init__(self):
                self.own = 2
            def m(self):
                return 'm'
        h = Holder()
        print(sorted(vars(h)), vars(h)['own'])
        print('shared' in vars(h), 'shared' in vars(Holder))
        print(sorted(h.__dict__))
        h.__dict__['dynamic'] = 9
        print(h.dynamic)
        def plain(x):
            return x
        print(plain.__name__, plain.__qualname__, plain.__annotations__)
        # ONE HANDLE PER OBJECT: `is` has to answer about the object, not
        # about which access it came back through.
        print(h.m.__self__ is h)
    """,
    "format_mini_language": """
        print(f"{3.14159:.2f}", f"{42:5d}", f"{42:<5}|", f"{42:^7}|")
        print(f"{42:*>6}", f"{255:x}", f"{255:X}", f"{255:#x}", f"{255:b}")
        print(f"{255:#b}", f"{8:o}", f"{1234567:,}", f"{1234567:_}")
        print(f"{1234567.891:,.2f}", f"{-1.5:08.2f}", f"{1.5:+.1f}")
        print(f"{1.5: .1f}", f"{0.25:%}", f"{1234.5:e}", f"{1234.5:.3g}")
        print(f"{'hi':>6}|", f"{'hello':.3}", f"{'hi':-^8}", f"{'a'!r:>5}|")
        width = 8
        print(f"{3.14159:{width}.3f}|")
        print("{} {} {}".format(1, 2, 3), "{0} {2} {1}".format('a', 'b', 'c'))
        print("{name}: {v:.1f}".format(name='x', v=2.55))
        print("{{literal}} {}".format(9), "{:>{}}".format('q', 5) + "|")
        print(format(3.14159, '.3f'), format(42, 'b'), format('hi'))
        print("{!r}".format('a'), "{0!r} {0}".format('b'))
        class Point:
            def __init__(self, x):
                self.x = x
            def __format__(self, spec):
                return 'P<' + spec + '>' + str(self.x)
            def __str__(self):
                return 'P' + str(self.x)
        p = Point(3)
        print(f"{p}", f"{p:.2f}", format(p, 'wide'))
    """,
    "iterator_protocol": """
        class Count:
            def __init__(self, n):
                self.n = n
                self.i = 0
            def __iter__(self):
                return self
            def __next__(self):
                if self.i >= self.n:
                    raise StopIteration
                self.i += 1
                return self.i
        print(list(Count(3)))
        it = Count(1)
        print(next(it))
        try:
            next(it)
        except StopIteration:
            print('StopIteration')
        for v in Count(2):
            print('v', v)
        print([x * 2 for x in Count(3)])
        class Seq:
            def __len__(self):
                return 3
            def __getitem__(self, i):
                if i >= 3:
                    raise IndexError
                return i * 10
        s = Seq()
        print(len(s), s[1], list(s), 20 in s, 5 in s)
        class Bare:
            def __getitem__(self, i):
                if i >= 2:
                    raise IndexError
                return i
        print(list(Bare()), 1 in Bare())
    """,
    "exceptions_leave_functions": """
        # An exception raised inside a call has to reach the CALLER's handler.
        # It leaves a compiled function the same way it leaves an `apy_add`:
        # with the flag set and a null result.
        def inner():
            raise ValueError('deep')
        def middle():
            inner()
            return 'unreachable'
        def guarded():
            try:
                middle()
            except ValueError as e:
                return 'caught ' + str(e)
            return 'no'
        print(guarded())
        try:
            middle()
        except ValueError as e:
            print('outer', e)
        class Box:
            def check(self, n):
                if n < 0:
                    raise ValueError('negative')
                return n
        b = Box()
        try:
            b.check(-1)
        except ValueError as e:
            print('method', e)
        print(b.check(2))
        def finallys():
            try:
                inner()
            finally:
                print('finally ran')
        try:
            finallys()
        except ValueError as e:
            print('after finally', e)
    """,
    "exception_chaining": """
        try:
            try:
                raise KeyError('inner')
            except KeyError:
                raise ValueError('outer')
        except ValueError as e:
            print(type(e).__name__, e.args,
                  type(e.__context__).__name__, e.__cause__)
        try:
            try:
                raise KeyError('k')
            except KeyError as k:
                raise ValueError('v') from k
        except ValueError as e:
            print(type(e.__cause__).__name__, type(e.__context__).__name__,
                  e.__suppress_context__)
        try:
            try:
                raise KeyError('k')
            except KeyError:
                raise ValueError('v') from None
        except ValueError as e:
            print(e.__cause__, e.__context__, e.__suppress_context__)
        try:
            raise ValueError('n')
        except ValueError as e:
            e.add_note('extra')
            print(e.__notes__, e.__traceback__ is None)
    """,
    "mutating_methods": """
        xs = [3, 1, 2]
        xs.insert(0, 9)
        xs.insert(99, 7)
        xs.insert(-99, 8)
        print(xs)
        ys = [3, 1, 2]
        ys.sort()
        print(ys)
        ys.sort(reverse=True)
        print(ys)
        zs = ['bb', 'a', 'ccc']
        zs.sort(key=len)
        print(zs)
        zs.reverse()
        copied = zs.copy()
        copied.clear()
        print(zs, copied)
        zs.extend(['x', 'y'])
        print(zs)
        d = {'a': 1}
        d.update({'b': 2})
        print(sorted(d.items()), d.setdefault('a', 9), d.setdefault('c', 3))
        print(sorted(d.items()))
        print('hi'.encode(), b'hi'.decode())
        print((255).bit_length(), (5).is_integer(), (2.0).is_integer())
        print((2.5).is_integer(), complex(1, 2).conjugate(), (5).conjugate())
    """,
    "star_kwargs_and_decorators": """
        def f(a, b=2, **kw):
            return (a, b, sorted(kw.items()))
        g = f
        print(g(1), g(1, 3, x=9, y=8), g(1, x=1))
        d = {'p': 1, 'q': 2}
        print(g(5, **d), g(5, b=7, **d))
        def h(*rest, **kw):
            return (rest, sorted(kw.items()))
        hh = h
        print(hh(1, 2, 3, k=1), hh())
        class Opts:
            def __init__(self, n, **opts):
                self.n = n
                self.opts = sorted(opts.items())
        made = Opts(1, colour='red', size=2)
        print(made.n, made.opts)

        def twice(fn):
            def wrapper(x):
                return fn(fn(x))
            return wrapper
        def shout(fn):
            def wrapper(x):
                return str(fn(x)) + '!'
            return wrapper
        @shout
        @twice
        def inc(n):
            return n + 1
        print(inc(1))
        def tag(label):
            def deco(fn):
                def wrapper(*a):
                    return (label, fn(*a))
                return wrapper
            return deco
        @tag('hi')
        def doubled(x):
            return x * 2
        print(doubled(3))
        def marked(cls):
            cls.mark = True
            return cls
        @marked
        class Plain:
            def m(self):
                return 'm'
        print(Plain.mark, Plain().m())
    """,
    "builtin_call_shapes": """
        # The builtins that are two functions wearing one name: the argument
        # count picks, so both shapes have to be checked.
        print(round(2.675, 2), round(2.5), round(2.5, 0), round(2.345, 2))
        print(round(1234.5678, -2), round(1234, -2), round(-2.5), round(0.5))
        print(int('ff', 16), int('101', 2), int('0x1f', 16), int('7'))
        print(sum([1, 2, 3], 10), sum([], 0.0), sum([1, 2]))
        print(min(3, 1, 2), max(3, 1, 2), min([3, 1, 2]), max([3, 1, 2]))
        print(min([], default=9), max([1, 2], default=9))
        print(list(zip()), list(zip([1, 2])), list(zip([1, 2], [3, 4], [5, 6])))
        class Base:
            pass
        class Derived(Base):
            pass
        print(issubclass(Derived, Base), issubclass(Base, Derived))
        class Holder:
            def __init__(self):
                self.x = 1
                self.y = 2
        h = Holder()
        print(sorted(vars(h).items()))
        setattr(h, 'z', 3)
        print(h.z, hasattr(h, 'z'))
        delattr(h, 'z')
        print(hasattr(h, 'z'))
        seen = [0]
        def tick():
            seen[0] += 1
            return seen[0] if seen[0] < 4 else 0
        print(list(iter(tick, 0)))
    """,
    "keyword_arguments": """
        def f(a, b=2, c=3):
            return (a, b, c)
        g = f
        print(f(1, c=30), g(1), g(1, c=30), g(1, b=20, c=30), g(c=9, a=8))
        class Point:
            def __init__(self, x, y=0, label='p'):
                self.x = x
                self.y = y
                self.label = label
            def moved(self, dx=0, dy=0):
                return (self.x + dx, self.y + dy, self.label)
        print(Point(1, label='q').label, Point(1, 2, 'r').moved(dx=1, dy=1))
        print(Point(1).moved(dy=5))
        # Through the ALIAS, so the callee is a value and the mismatch is
        # reported where CPython reports it -- at run time. Calling `f` by
        # name is checked at compile time instead, which is a difference in
        # WHEN and not in what.
        try:
            g(1, z=5)
        except TypeError as e:
            print('TypeError', e)
        try:
            g(1, a=5)
        except TypeError as e:
            print('TypeError', e)
        try:
            g(b=1)
        except TypeError as e:
            print('TypeError', e)
    """,
    "arithmetic_and_kinds": """
        print(1 + 2, 3.5, True, False, None)
        print(7 // 2, -7 // 2, 7 % 3, -7 % 3)
        print(1 / 4, 2 ** 10, -5, +5, ~5)
        print(True + 1, 1 == True, 1.0 == 1)
        print(1 and 2, 0 or 'x', not 0)
        print(1 < 2 < 3, 1 < 2 > 3)
        print(type(1).__name__, type(1.0).__name__)
        print(type(True).__name__, type(None).__name__, type('a').__name__)
    """,
    "strings": """
        s = 'abc'
        print(s + 'de', s * 2, 3 * s, len(s))
        print(s[0], s[-1], s[1:], s[:2], s[::-1])
        print('b' in s, 'z' in s)
        print(repr('a"b'), repr("it's"))
        print('  pad  '.strip(), 'A,B'.split(','), '-'.join(['x', 'y']))
        print('abc'.upper(), 'ABC'.lower(), 'abc'.replace('b', 'X'))
        print('abc'.find('b'), 'abc'.startswith('ab'), '7'.zfill(3))
    """,
    "sequences": """
        xs = [1, 2, 3]
        print(xs, xs[0], xs[-1], len(xs))
        xs.append(4)
        xs[0] = 'a'
        print(xs, xs.pop(), xs.index(2), xs.count(2))
        t = (1, 'two', 3.5)
        print(t, t[1], len(t), (7,), ())
        print([1, 2] + [3], (1,) + (2,), [0] * 3)
        print(1 in xs, 9 in xs, [1] in xs)
        print([1, 2] == [1, 2], [1, 2] == (1, 2), [1, 2] < [1, 3])
        print(sorted([3, 1, 2]), min([3, 1]), max([3, 1]), sum([1, 2]))
        print(list(reversed([1, 2])), list(enumerate('ab')))
        print(list(zip([1, 2], 'ab')), list(range(3)))
    """,
    "dicts_and_sets": """
        d = {'a': 1, 'b': 2}
        d['c'] = 3
        d['a'] = 9
        print(d, d['a'], len(d), 'a' in d, 'z' in d)
        print(list(d.keys()), list(d.values()), list(d.items()))
        print(d.get('a'), d.get('z'), d.get('z', 0))
        print({}, {1: [2], (3,): 'x'}, {'a': 1} == {'a': 1})
        xs = {1, 2, 3}
        xs.add(4)
        print(sorted(xs), len(xs), 2 in xs, type(xs).__name__)
        print(sorted({1, 2} | {2, 3}), sorted({1, 2} & {2, 3}))
        print(set(), frozenset([1, 2]) == frozenset([2, 1]))
    """,
    "control_flow": """
        total = 0
        for i in range(5):
            if i == 3:
                continue
            total = total + i
        print(total)
        n = 0
        while n < 3:
            n += 1
        print(n)
        for v in [10, 20]:
            print(v)
        else:
            print('for-else')
        for v in [10, 20]:
            break
        else:
            print('not reached')
        print('yes' if n else 'no')
        if n > 99:
            print('big')
        else:
            print('small')
    """,
    "exceptions": """
        try:
            raise ValueError('boom')
        except ValueError as e:
            print('caught', e, type(e).__name__)
        try:
            print(1 / 0)
        except ZeroDivisionError as e:
            print('zde', e)
        try:
            raise KeyError('k')
        except LookupError:
            print('base class caught it')
        try:
            print(1 + 'a')
        except TypeError as e:
            print('te', e)
        try:
            print('fine')
        except ValueError:
            print('not reached')
        else:
            print('else ran')
        finally:
            print('finally ran')
        try:
            [1][5]
        except IndexError as e:
            print('idx', e)
        print(repr(ValueError('v')))
    """,
    "functions_and_globals": """
        top = 5
        items = [1, 2]
        def read():
            return top, items
        print(read())
        count = 0
        def bump():
            global count
            count = count + 1
        bump()
        bump()
        print(count)
        def shadow():
            top = 99
            return top
        print(shadow(), top)
        def greet(name, greeting='hi'):
            return greeting + ' ' + name
        print(greet('a'), greet('a', 'yo'), greet(greeting='hey', name='b'))
        def collect(xs=[]):
            xs.append(1)
            return xs
        print(collect(), collect())
        def wide(a, *rest):
            return a, rest
        print(wide(1), wide(1, 2, 3))
    """,
    "comprehensions_and_unpacking": """
        print([x * 2 for x in [1, 2, 3]])
        print([x for x in range(6) if x % 2 == 0])
        print([(a, b) for a in [1, 2] for b in 'xy'])
        print({k: v for k, v in [('a', 1), ('b', 2)]})
        print(sorted({x * 2 for x in [1, 2, 2]}))
        print(sum(x for x in [1, 2, 3]))
        a, b = (1, 2)
        print(a, b)
        for i, ch in enumerate('hi'):
            print(i, ch)
        for k, v in {'p': 1}.items():
            print(k, v)
    """,
    "bytes": """
        b = b'ab'
        print(b, len(b), b[0], b[-1], b[1:], b + b'cd', b * 2)
        print(b == b'ab', b == 'ab', b < b'ac', b'a' in b, 97 in b)
        print(type(b).__name__, b'', b'q' * 0)
        print(b'abc'[::-1], b'abcdef'[1:5:2])
        print({b'k': 1}[b'k'], b'x' in {b'x': 1})
        print(repr(b'a\\tb\\nc'), repr(b"it's"))
        print(sorted([b'c', b'a', b'b']))
    """,
    "del_and_walrus": """
        d = {'a': 1, 'b': 2}
        del d['a']
        xs = [1, 2, 3]
        del xs[1]
        del xs[-1]
        print(d, xs)
        top = 5
        del top
        try:
            print(top)
        except NameError as e:
            print('NameError', e)
        try:
            del d['zz']
        except KeyError as e:
            print('KeyError', e)
        n = 0
        while (n := n + 1) < 4:
            print('walrus', n)
        print([y for x in [1, 2, 3] if (y := x * 2) > 2])
    """,
    "star_args": """
        xs = [1, 2, 3]
        def take(*a):
            return a
        print(take(*xs))
        def two(a, b):
            return a - b
        print(two(*[10, 4]), two(1, *[9]))
        def mixed(a, *rest):
            return a, rest
        print(mixed(*xs))
        print(take(*'ab'), take(*(7, 8)))
    """,
    "big_integers": """
        big = 9223372036854775808
        print(big, big - 1, -big, big * 2)
        print(2 ** 100, 10 ** 30)
        n = 1
        for i in range(1, 26):
            n = n * i
        print(n)
        print(big // 2, big % 7, big > 5, big == big)
        back = (big + 1) - big
        print(back, back == 1, type(back).__name__)
        d = {back: 'x'}
        print(d[1], 1 in d, sorted([big, 1, -big]))
        print(str(big), len(str(2 ** 100)))
        print(int('123456789012345678901234567890') + 1)
    """,
    "exception_payloads": """
        class E(Exception):
            pass
        def move(v):
            try:
                raise E(v)
            except E as e:
                return e.args[0]
        for original in [42, 'abc', [1, 2], None, 3.5, b'ab', 9223372036854775808]:
            moved = move(original)
            print(moved, moved == original, type(moved).__name__)
        try:
            raise ValueError()
        except ValueError as e:
            print(repr(e), e.args, repr(str(e)))
        try:
            raise ValueError(None)
        except ValueError as e:
            print(repr(e), e.args, str(e))
        try:
            assert False
        except AssertionError as e:
            print(repr(e), e.args)
    """,
    "complex_numbers": """
        print(1j, 3+4j, (1+2j))
        print(complex(0,2), complex(1,2), complex(1,-2), complex(-0.0,2))
        print(complex(0,-0.0), complex(0,0), complex(1.5,0), complex(), complex(5))
        print((1+2j)+(3+4j), (1+2j)*(3+4j), (1+2j)-(3+4j), (1+2j)/(3+4j))
        print((1+2j)==(1+2j), (1+2j)==1, complex(1,0)==1, 1j=='a')
        print((1+2j).real, (1+2j).imag, type(1j).__name__, bool(0j), bool(1j))
        print(1j + 2, 2 + 1j, 1j * 2.0, -(1+2j), +(1+2j))
        try:
            print(1j < 2j)
        except TypeError as e:
            print('TypeError', e)
        try:
            print(1j / 0)
        except ZeroDivisionError:
            print('ZeroDivisionError')
        print([1j, 2+3j], {1j: 'a'}[1j], 1j in [1j])
    """,
    "builtins_as_values": """
        print(sorted([3, 1, 2], key=str))
        print(sorted(['bb', 'a', 'ccc'], key=len))
        f = repr
        print(f('x'), f(1))
        g = abs
        print(g(-3))
        def apply(fn, v):
            return fn(v)
        print(apply(len, 'abcd'), apply(repr, 'q'), apply(str, 9))
        print(apply(hex, 255), apply(ord, 'A'), apply(chr, 66))
    """,
    "lambdas_and_keys": """
        f = lambda x: x * 2
        g = lambda a, b: a + b
        print(f(3), g(1, 2))
        n = 10
        h = lambda: n
        print(h(), (lambda x=5: x)(), (lambda x=5: x)(9))
        def make(k):
            return lambda v: v * k
        print(make(3)(4), make(10)(4))
        print(sorted([3, 1, 2], key=lambda v: -v))
        print(sorted([3, 1, 2], reverse=True))
        print(sorted(['bb', 'a', 'ccc'], key=len, reverse=True))
        print(min([3, 1, 2], key=lambda v: -v), max([3, 1, 2], key=lambda v: -v))
        print(sorted([(1, 'b'), (1, 'a'), (0, 'c')], key=lambda p: p[0]))
        adders = [lambda v, k=i: v + k for i in range(3)]
        print([a(10) for a in adders])
    """,
    "iterators": """
        it = iter([1, 2, 3])
        print(next(it), next(it), next(it))
        try:
            next(it)
        except StopIteration:
            print('StopIteration')
        print(next(iter([]), 'dflt'))
        s = iter('ab')
        print(next(s), next(s))
        it2 = iter([7, 8])
        print(list(it2), list(it2))
        it3 = iter([1, 2, 3])
        next(it3)
        for v in it3:
            print(v)
        print(sum(iter([1, 2, 3])), sorted(iter([3, 1])))
        print(dict(), dict([('a', 1), ('b', 2)]))
        print(bytes(), bytes([1, 255, 16]), bytes(b'ab'))
    """,
    "star_displays_and_augmented": """
        xs = [1, 2]
        print([*xs, 3], [0, *xs], [*xs, *xs])
        print((*xs, 3), sorted({*xs, 3}))
        print([*'ab'], [*{'k': 1}])
        log = []
        def idx():
            log.append('idx')
            return 0
        ys = [10]
        ys[idx()] += 5
        print(ys, log)
        d = {'a': 1}
        d['a'] += 2
        print(d)
        class P:
            def __init__(self):
                self.n = 1
        p = P()
        p.n += 4
        print(p.n)
        zs = [[1], [2]]
        zs[0] += [9]
        print(zs)
    """,
    "fstrings": """
        n = 42
        s = 'ab'
        print(f'n={n} s={s}')
        print(f'{n}', f'{s!r}', f'', f'no interp')
        print(f'{n + 1} {[1, 2]}')
    """,
}


def cpython(src: str) -> list[str]:
    """What CPython prints. The oracle, with no compensation of any kind --
    every divergence the runtime has is one this corpus avoids rather than one
    the comparison papers over.

    REAL STDOUT, through the real `print`. A stub that joined its arguments
    with a space stood in for it once, and it was not the oracle it claimed to
    be: it ignored `sep` and `end`, so a program using either was compared
    against an answer CPython does not give. Redirecting the stream costs
    nothing and is the thing itself.
    """
    stream = StringIO()
    with redirect_stdout(stream):
        # `dont_inherit=True`, because THIS MODULE says `from __future__
        # import annotations` and `compile` inherits future flags from its
        # caller. Every corpus program was compiled in a language mode a
        # script does not use -- annotations stringified -- so the oracle
        # disagreed with CPython about any program that reads
        # `__annotations__`, and it disagreed silently.
        exec(compile(src, "<case>", "exec", dont_inherit=True),
             {"__name__": "__main__"})
    return stream.getvalue().split("\n")[:-1]


def compile_it(src: str, tmp_path: Path, optimise: bool = False):
    path = tmp_path / "prog.py"
    path.write_text(src, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path, optimise=optimise), sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    return result.module


@harness.cases("name", sorted(PROGRAMS))
class TestEveryPathAgrees:
    def source(self, name: str) -> str:
        return textwrap.dedent(PROGRAMS[name]).strip() + "\n"

    def test_the_interpreter_matches_cpython(self, name, tmp_path):
        src = self.source(name)
        out = StringIO()
        Interpreter(compile_it(src, tmp_path), out=out).run("main")
        assert out.getvalue().split("\n")[:-1] == cpython(src)

    def test_the_interpreter_matches_cpython_optimised(self, name, tmp_path):
        """The same, on optimised IR. This is what catches a pass that changes
        meaning -- which no amount of testing a pass in isolation will find,
        because a pass can be individually correct and wrong in combination."""
        src = self.source(name)
        out = StringIO()
        Interpreter(compile_it(src, tmp_path, optimise=True), out=out).run("main")
        assert out.getvalue().split("\n")[:-1] == cpython(src)

    @harness.needs("cc")
    def test_the_c_backend_matches_cpython(self, name, tmp_path):
        from asmpython.backend import get, load_builtin
        from asmpython.target import get as get_target
        load_builtin()
        src = self.source(name)
        module = compile_it(src, tmp_path)
        c_file = tmp_path / "out.c"
        c_file.write_bytes(get("c").emit(module, get_target("c"))["out.c"])
        exe = tmp_path / "out.exe"
        built = subprocess.run([HAS_CC, str(c_file), "-o", str(exe)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        # UTF-8, NOT THE LOCALE ENCODING. A str is stored as UTF-8 by this
        # runtime and the compiled program writes those bytes straight out;
        # decoding them as cp1252 turns every non-ASCII character into
        # mojibake and compares it against CPython's correct output. The
        # conformance shim had the same bug, and it failed cases the compiler
        # was getting exactly right.
        ran = subprocess.run([str(exe)], capture_output=True, text=True,
                             encoding="utf-8")
        assert ran.stdout.split("\n")[:-1] == cpython(src)
