"""Ordinary Python, run three ways and compared against CPython.

`test_endtoend.py` does this for the statically typed subset. This does it for
the DYNAMIC path -- the one a Python script actually takes, where every value
is a runtime object and every operation a call into `link/objects.py`.

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
        exec(compile(src, "<case>", "exec"), {"__name__": "__main__"})
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
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        assert ran.stdout.split("\n")[:-1] == cpython(src)
