"""Generate control-flow conformance probes.

Every construct in this file is one that *suspends or diverts* control rather
than running straight through, and each one is lowered by a different mechanism:
`with` needs an unwind action bound to a scope exit, generators need a resumable
frame, comprehensions need their own scope, closures need captured cells,
exception chaining needs a live `__context__`/`__cause__` link, and `else` on a
loop needs the break edge distinguished from the exhaustion edge.

That variety is why they belong in separate probes. `vm_finally_runs_on_return`,
`_on_break` and `_on_continue` already exist as three probes precisely because
fixing the return path does not fix the loop paths -- they unwind through the
loop stack instead. The same argument applies to every pair below: a `with`
whose body returns, a generator that is closed early, and a comprehension that
shadows an enclosing name are three unrelated pieces of lowering that a single
"control flow works" case would fuse into one uninformative verdict.

FAILURE_AUDIT.md rank 16 (`finally does not run on return`, 4 cases), rank 12
(`closures / callable values not modelled`, 7 cases) and the 64-case
un-root-caused bucket all live here.

Usage: python gen_flow_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# with / context managers
# ---------------------------------------------------------------------------

case("flow_with_enter_exit_order", "__enter__ and __exit__ bracket the body", r'''
class Trace:
    def __enter__(self):
        print("enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit")
        return False


with Trace():
    print("body")
''')

case("flow_with_as_binds_enter_result", "`as` binds what __enter__ returned", r'''
class Resource:
    def __enter__(self):
        return "handle"

    def __exit__(self, exc_type, exc, tb):
        return False


with Resource() as handle:
    print(handle)
''')

case("flow_with_exit_runs_on_exception", "__exit__ runs when the body raises", r'''
class Trace:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit ran")
        return False


try:
    with Trace():
        raise ValueError("boom")
except ValueError:
    print("propagated")
''')

case("flow_with_exit_true_suppresses", "__exit__ returning True swallows the error", r'''
class Swallow:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return True


with Swallow():
    raise ValueError("boom")
print("continued")
''')

case("flow_with_exit_sees_exception_type", "__exit__ receives the exception it is handling", r'''
class Inspect:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print(exc_type.__name__)
        print(str(exc))
        return True


with Inspect():
    raise ValueError("details")
''')

case("flow_with_exit_runs_on_return", "__exit__ runs on the return path", r'''
class Trace:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit ran")
        return False


def f():
    with Trace():
        return "returned"


print(f())
''')

case("flow_with_exit_runs_on_break", "__exit__ runs on the break path", r'''
class Trace:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit ran")
        return False


for i in [1, 2, 3]:
    with Trace():
        if i == 2:
            break
        print("body", i)
print("done")
''')

case("flow_with_multiple_managers", "one with statement can hold several managers", r'''
class Named:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        print("enter " + self.name)
        return self.name

    def __exit__(self, exc_type, exc, tb):
        print("exit " + self.name)
        return False


with Named("a") as first, Named("b") as second:
    print(first + second)
''')

case("flow_with_nested_exit_order", "nested managers exit innermost first", r'''
class Named:
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        print("exit " + self.name)
        return False


with Named("outer"):
    with Named("inner"):
        print("body")
''')

case("flow_contextmanager_decorator", "@contextmanager turns a generator into a manager", r'''
import contextlib


@contextlib.contextmanager
def section(name):
    print("open " + name)
    yield name.upper()
    print("close " + name)


with section("job") as label:
    print(label)
''')


# ---------------------------------------------------------------------------
# generators
# ---------------------------------------------------------------------------

case("flow_generator_yields_in_order", "a generator yields lazily in order", r'''
def counter():
    yield 1
    yield 2
    yield 3


for v in counter():
    print(v)
''')

case("flow_generator_is_lazy", "a generator body does not run until iterated", r'''
def noisy():
    print("started")
    yield 1


gen = noisy()
print("created")
print(next(gen))
''')

case("flow_generator_next_stopiteration", "an exhausted generator raises StopIteration", r'''
def one():
    yield 1


gen = one()
print(next(gen))
try:
    next(gen)
    print("no stop")
except StopIteration:
    print("stopped")
''')

case("flow_generator_keeps_state", "a generator resumes with its locals intact", r'''
def running_total(values):
    total = 0
    for v in values:
        total = total + v
        yield total


print(list(running_total([1, 2, 3])))
''')

case("flow_generator_send", "send() delivers a value into the generator", r'''
def echo():
    received = yield "ready"
    yield "got:" + str(received)


gen = echo()
print(next(gen))
print(gen.send(7))
''')

case("flow_generator_return_value", "a generator's return value rides on StopIteration", r'''
def with_result():
    yield 1
    return "final"


gen = with_result()
print(next(gen))
try:
    next(gen)
except StopIteration as stop:
    print(stop.value)
''')

case("flow_yield_from_delegates", "yield from forwards a sub-generator's values", r'''
def inner():
    yield 1
    yield 2


def outer():
    yield 0
    yield from inner()
    yield 3


print(list(outer()))
''')

case("flow_generator_close_runs_finally", "close() unwinds the generator's finally", r'''
def guarded():
    try:
        yield 1
        yield 2
    finally:
        print("cleaned up")


gen = guarded()
print(next(gen))
gen.close()
print("closed")
''')

case("flow_generator_expression", "a generator expression is consumed lazily", r'''
squares = (n * n for n in [1, 2, 3])
print(sum(squares))
''')

case("flow_generator_in_comprehension_arg", "a generator expression works as a sole argument", r'''
print(max(len(w) for w in ["a", "abc", "ab"]))
''')


# ---------------------------------------------------------------------------
# comprehension scoping
# ---------------------------------------------------------------------------

case("flow_comprehension_var_does_not_leak", "a comprehension's loop name stays inside it", r'''
n = "outer"
squares = [n * n for n in [1, 2, 3]]
print(squares)
print(n)
''')

case("flow_comprehension_reads_enclosing", "a comprehension reads enclosing names", r'''
factor = 10
print([v * factor for v in [1, 2]])
''')

case("flow_comprehension_condition", "a comprehension filters with its if clause", r'''
print([v for v in range(6) if v % 2 == 0])
''')

case("flow_comprehension_nested_loops", "nested for clauses iterate left to right", r'''
print([(a, b) for a in [1, 2] for b in ["x", "y"]])
''')

case("flow_comprehension_nested_inside", "a comprehension can contain a comprehension", r'''
grid = [[1, 2], [3, 4]]
print([[cell * 2 for cell in row] for row in grid])
''')

case("flow_dict_comprehension", "a dict comprehension builds keys and values", r'''
print({k: len(k) for k in ["a", "bb"]})
''')

case("flow_set_comprehension", "a set comprehension removes duplicates", r'''
print(sorted({v % 3 for v in range(7)}))
''')

case("flow_comprehension_in_method_sees_self", "a comprehension in a method reaches self", r'''
class Scaler:
    def __init__(self, factor):
        self.factor = factor

    def scale(self, values):
        return [v * self.factor for v in values]


print(Scaler(3).scale([1, 2]))
''')

case("flow_walrus_in_comprehension", "a walrus binding in a comprehension outlives it", r'''
values = [1, 5, 3]
kept = [seen for v in values if (seen := v * 2) > 4]
print(kept)
print(seen)
''')

case("flow_comprehension_over_dict_items", "iterating .items() unpacks two names", r'''
source = {"a": 1, "b": 2}
print([k + str(v) for k, v in source.items()])
''')


# ---------------------------------------------------------------------------
# closures
# ---------------------------------------------------------------------------

case("flow_closure_reads_enclosing", "a nested function reads the enclosing local", r'''
def outer():
    message = "captured"

    def inner():
        return message

    return inner()


print(outer())
''')

case("flow_closure_returned_keeps_binding", "a returned closure keeps its captured value", r'''
def make_adder(n):
    def add(v):
        return v + n

    return add


add5 = make_adder(5)
add10 = make_adder(10)
print(add5(1))
print(add10(1))
''')

case("flow_closure_nonlocal_write", "nonlocal rebinds the enclosing local", r'''
def counter():
    count = 0

    def bump():
        nonlocal count
        count = count + 1
        return count

    bump()
    bump()
    return count


print(counter())
''')

case("flow_closure_shares_one_cell", "two closures over one variable share it", r'''
def make_pair():
    value = 0

    def setter(v):
        nonlocal value
        value = v

    def getter():
        return value

    return setter, getter


put, take = make_pair()
put(42)
print(take())
''')

case("flow_closure_in_loop_late_binding", "closures made in a loop share the loop variable", r'''
fns = []
for i in range(3):
    fns.append(lambda: i)
print([f() for f in fns])
''')

case("flow_closure_default_arg_early_binding", "a default argument captures at definition time", r'''
fns = []
for i in range(3):
    fns.append(lambda bound=i: bound)
print([f() for f in fns])
''')

case("flow_closure_two_levels_deep", "a closure reaches two scopes out", r'''
def level1():
    a = "one"

    def level2():
        b = "two"

        def level3():
            return a + "-" + b

        return level3()

    return level2()


print(level1())
''')

case("flow_closure_over_mutable", "a closure mutates a captured container", r'''
def collector():
    seen = []

    def add(v):
        seen.append(v)
        return len(seen)

    add("a")
    add("b")
    return seen


print(collector())
''')


# ---------------------------------------------------------------------------
# exceptions: chaining, else, nesting
# ---------------------------------------------------------------------------

case("flow_raise_from_sets_cause", "raise ... from ... records __cause__", r'''
try:
    try:
        raise ValueError("inner")
    except ValueError as inner:
        raise TypeError("outer") from inner
except TypeError as outer:
    print(str(outer))
    print(type(outer.__cause__).__name__)
    print(str(outer.__cause__))
''')

case("flow_implicit_exception_context", "an error raised while handling keeps __context__", r'''
try:
    try:
        raise ValueError("first")
    except ValueError:
        raise TypeError("second")
except TypeError as err:
    print(str(err))
    print(type(err.__context__).__name__)
''')

case("flow_try_else_runs_without_error", "try/else runs the else only on success", r'''
def attempt(fail):
    try:
        if fail:
            raise ValueError("x")
    except ValueError:
        return "caught"
    else:
        return "clean"


print(attempt(False))
print(attempt(True))
''')

case("flow_except_matches_base_class", "except catches a subclass of the named type", r'''
class AppError(Exception):
    pass


class DiskError(AppError):
    pass


try:
    raise DiskError("disk")
except AppError as err:
    print(type(err).__name__)
    print(str(err))
''')

case("flow_except_order_first_match", "the first matching except clause wins", r'''
try:
    raise ValueError("v")
except TypeError:
    print("type")
except ValueError:
    print("value")
except Exception:
    print("generic")
''')

case("flow_bare_raise_reraises", "a bare raise re-raises the active exception", r'''
try:
    try:
        raise ValueError("original")
    except ValueError:
        print("handling")
        raise
except ValueError as err:
    print("outer saw " + str(err))
''')

case("flow_custom_exception_attribute", "a custom exception carries its own fields", r'''
class HttpError(Exception):
    def __init__(self, status):
        super().__init__("status " + str(status))
        self.status = status


try:
    raise HttpError(404)
except HttpError as err:
    print(err.status)
    print(str(err))
''')

case("flow_finally_runs_after_except", "finally runs after a handled exception", r'''
def f():
    try:
        raise ValueError("x")
    except ValueError:
        return "handled"
    finally:
        print("finally")


print(f())
''')

case("flow_nested_try_inner_handles", "an inner try handles before the outer sees it", r'''
try:
    try:
        raise ValueError("inner")
    except ValueError:
        print("inner handled")
except ValueError:
    print("outer handled")
print("done")
''')

case("flow_exception_group_except_star", "except* handles an ExceptionGroup (3.11+)", r'''
try:
    raise ExceptionGroup("group", [ValueError("a"), TypeError("b")])
except* ValueError as group:
    print("values", len(group.exceptions))
except* TypeError as group:
    print("types", len(group.exceptions))
''')


# ---------------------------------------------------------------------------
# else on loops
# ---------------------------------------------------------------------------

case("flow_for_else_runs_when_exhausted", "for/else runs when the loop is not broken", r'''
for v in [1, 2]:
    print(v)
else:
    print("exhausted")
''')

case("flow_for_else_skipped_on_break", "for/else is skipped after a break", r'''
for v in [1, 2, 3]:
    if v == 2:
        break
    print(v)
else:
    print("exhausted")
print("after")
''')

case("flow_while_else_runs_when_false", "while/else runs when the condition fails", r'''
n = 0
while n < 2:
    n = n + 1
else:
    print("condition ended it")
print(n)
''')

case("flow_while_else_skipped_on_break", "while/else is skipped after a break", r'''
n = 0
while True:
    n = n + 1
    if n == 2:
        break
else:
    print("not reached")
print(n)
''')

case("flow_for_else_empty_iterable", "for/else runs even for an empty iterable", r'''
for v in []:
    print(v)
else:
    print("else ran")
''')


# ===========================================================================
# Wave 2. Three whole constructs had no probe at all after wave 1, and each is
# a distinct lowering problem rather than a variation on one:
#
#   async/await   -- a coroutine is a resumable frame driven by an event loop,
#                    not a call. `asyncio.run` has to exist AND the frame has
#                    to survive suspension.
#   match         -- structural pattern matching compiles to a decision tree
#                    over shapes, with binding as a side effect of matching.
#                    FAILURE_AUDIT rank 19 is "parser: syntax not supported".
#   decorators    -- a decorator is a call whose result REPLACES a function
#                    binding, so it needs first-class function values, which
#                    FAILURE_AUDIT rank 12 says are not modelled.
#
# Argument-passing forms are here for the same reason: `*args`, `**kwargs`,
# keyword-only and positional-only parameters each change the calling
# convention rather than the body.
# ===========================================================================

case("flow_async_run_returns_value", "asyncio.run returns the coroutine's result", r'''
import asyncio


async def compute():
    return 21 * 2


print(asyncio.run(compute()))
''')

case("flow_async_await_chain", "await suspends and resumes through a chain", r'''
import asyncio


async def inner():
    return "inner"


async def outer():
    value = await inner()
    return "outer+" + value


print(asyncio.run(outer()))
''')

case("flow_async_sleep_orders_output", "await asyncio.sleep yields to the loop", r'''
import asyncio


async def main():
    print("before")
    await asyncio.sleep(0)
    print("after")


asyncio.run(main())
''')

case("flow_async_gather_preserves_order", "gather returns results in argument order", r'''
import asyncio


async def value(n):
    await asyncio.sleep(0)
    return n


async def main():
    return await asyncio.gather(value(1), value(2), value(3))


print(asyncio.run(main()))
''')

case("flow_async_for_over_async_generator", "async for drives an async generator", r'''
import asyncio


async def counter():
    for n in [1, 2, 3]:
        yield n


async def main():
    seen = []
    async for n in counter():
        seen.append(n)
    return seen


print(asyncio.run(main()))
''')

case("flow_async_with_context_manager", "async with runs __aenter__/__aexit__", r'''
import asyncio


class Resource:
    async def __aenter__(self):
        print("aenter")
        return "handle"

    async def __aexit__(self, exc_type, exc, tb):
        print("aexit")
        return False


async def main():
    async with Resource() as handle:
        print(handle)


asyncio.run(main())
''')

case("flow_match_literal_patterns", "match selects on literal patterns", r'''
def name_for(code):
    match code:
        case 200:
            return "ok"
        case 404:
            return "missing"
        case _:
            return "other"


print(name_for(200))
print(name_for(404))
print(name_for(500))
''')

case("flow_match_sequence_pattern", "a sequence pattern destructures and binds", r'''
def describe(point):
    match point:
        case [0, 0]:
            return "origin"
        case [x, 0]:
            return "on x at " + str(x)
        case [x, y]:
            return "at " + str(x) + "," + str(y)
        case _:
            return "unknown"


print(describe([0, 0]))
print(describe([3, 0]))
print(describe([1, 2]))
''')

case("flow_match_mapping_pattern", "a mapping pattern matches keys and binds values", r'''
def route(payload):
    match payload:
        case {"action": "add", "value": v}:
            return "add " + str(v)
        case {"action": "quit"}:
            return "quit"
        case _:
            return "unhandled"


print(route({"action": "add", "value": 5}))
print(route({"action": "quit"}))
print(route({"action": "other"}))
''')

case("flow_match_class_pattern", "a class pattern matches by type and attribute", r'''
class Point:
    __match_args__ = ("x", "y")

    def __init__(self, x, y):
        self.x = x
        self.y = y


def describe(value):
    match value:
        case Point(0, 0):
            return "origin"
        case Point(x, y):
            return str(x) + "/" + str(y)
        case _:
            return "not a point"


print(describe(Point(0, 0)))
print(describe(Point(1, 2)))
print(describe("x"))
''')

case("flow_match_guard", "a case guard further constrains a pattern", r'''
def classify(n):
    match n:
        case v if v < 0:
            return "negative"
        case 0:
            return "zero"
        case _:
            return "positive"


print(classify(-1))
print(classify(0))
print(classify(5))
''')

case("flow_match_or_pattern", "an or-pattern accepts several alternatives", r'''
def weekend(day):
    match day:
        case "sat" | "sun":
            return True
        case _:
            return False


print(weekend("sat"))
print(weekend("mon"))
''')

case("flow_match_capture_rest", "a starred sequence pattern captures the rest", r'''
def head_tail(items):
    match items:
        case [first, *rest]:
            return (first, rest)
        case []:
            return ("empty", [])


print(head_tail([1, 2, 3]))
print(head_tail([]))
''')

case("flow_decorator_wraps_result", "a decorator replaces the function binding", r'''
def shout(fn):
    def wrapper(value):
        return fn(value).upper()

    return wrapper


@shout
def greet(name):
    return "hi " + name


print(greet("ada"))
''')

case("flow_decorator_with_arguments", "a decorator factory takes its own arguments", r'''
def repeat(times):
    def decorate(fn):
        def wrapper(value):
            out = ""
            for _ in range(times):
                out = out + fn(value)
            return out

        return wrapper

    return decorate


@repeat(3)
def dot(value):
    return value


print(dot("ab"))
''')

case("flow_decorator_stacked_order", "stacked decorators apply bottom-up", r'''
def outer(fn):
    def wrapper():
        return "outer(" + fn() + ")"

    return wrapper


def inner(fn):
    def wrapper():
        return "inner(" + fn() + ")"

    return wrapper


@outer
@inner
def base():
    return "base"


print(base())
''')

case("flow_decorator_preserves_metadata", "functools.wraps keeps __name__", r'''
import functools


def trace(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@trace
def original(a):
    """Doc text."""
    return a


print(original.__name__)
print(original.__doc__)
print(original(1))
''')

case("flow_decorator_on_method", "a decorator applies to a method", r'''
def double(fn):
    def wrapper(self, value):
        return fn(self, value) * 2

    return wrapper


class Calc:
    @double
    def add_one(self, value):
        return value + 1


print(Calc().add_one(3))
''')

case("flow_class_decorator", "a class decorator replaces the class binding", r'''
def tagged(cls):
    cls.tag = "tagged"
    return cls


@tagged
class Widget:
    pass


print(Widget.tag)
''')

case("flow_keyword_only_parameters", "parameters after * are keyword-only", r'''
def build(a, *, mode="fast"):
    return str(a) + "-" + mode


print(build(1))
print(build(1, mode="slow"))
try:
    build(1, "slow")
    print("positional accepted")
except TypeError:
    print("positional refused")
''')

case("flow_positional_only_parameters", "parameters before / are positional-only", r'''
def build(a, /, b):
    return str(a) + "-" + str(b)


print(build(1, 2))
print(build(1, b=3))
try:
    build(a=1, b=2)
    print("keyword accepted")
except TypeError:
    print("keyword refused")
''')

case("flow_star_args_collects", "*args collects the extra positionals", r'''
def total(first, *rest):
    return first + sum(rest)


print(total(1))
print(total(1, 2, 3))
''')

case("flow_kwargs_collects", "**kwargs collects the extra keywords", r'''
def describe(**fields):
    return ",".join(k + "=" + str(v) for k, v in fields.items())


print(describe(a=1, b=2))
''')

case("flow_args_forwarded_through", "*args/**kwargs forward unchanged", r'''
def target(a, b, c=0):
    return a + b + c


def forward(*args, **kwargs):
    return target(*args, **kwargs)


print(forward(1, 2))
print(forward(1, 2, c=3))
''')

case("flow_default_evaluated_once", "a mutable default is shared between calls", r'''
def accumulate(value, into=[]):
    into.append(value)
    return into


print(accumulate(1))
print(accumulate(2))
''')

case("flow_default_uses_none_sentinel", "the None-sentinel idiom gives a fresh list", r'''
def accumulate(value, into=None):
    if into is None:
        into = []
    into.append(value)
    return into


print(accumulate(1))
print(accumulate(2))
''')

case("flow_global_statement_rebinds", "global rebinds a module-level name", r'''
counter = 0


def bump():
    global counter
    counter = counter + 1


bump()
bump()
print(counter)
''')

case("flow_assert_message", "a failing assert raises with its message", r'''
try:
    assert 1 == 2, "values differ"
    print("no error")
except AssertionError as err:
    print(str(err))
''')

case("flow_conditional_expression", "a conditional expression picks one branch", r'''
def label(n):
    return "big" if n > 10 else "small"


print(label(50))
print(label(1))
''')

case("flow_stopiteration_in_generator_is_runtimeerror", "a leaked StopIteration becomes RuntimeError", r'''
def gen():
    raise StopIteration("leaked")
    yield 1


try:
    list(gen())
    print("no error")
except RuntimeError:
    print("converted to RuntimeError")
''')

case("flow_try_finally_inside_generator", "a generator's finally runs at exhaustion", r'''
def guarded():
    try:
        yield 1
        yield 2
    finally:
        print("finally ran")


print(list(guarded()))
''')

case("flow_recursive_generator", "a generator may delegate to itself", r'''
def flatten(items):
    for item in items:
        if isinstance(item, list):
            yield from flatten(item)
        else:
            yield item


print(list(flatten([1, [2, [3, 4]], 5])))
''')

case("flow_mutual_recursion", "two functions may recurse through each other", r'''
def is_even(n):
    if n == 0:
        return True
    return is_odd(n - 1)


def is_odd(n):
    if n == 0:
        return False
    return is_even(n - 1)


print(is_even(10))
print(is_odd(7))
''')

case("flow_lambda_captures_and_calls", "a lambda closes over an enclosing name", r'''
def make_scaler(factor):
    return lambda v: v * factor


print(make_scaler(3)(4))
print((lambda a, b=2: a + b)(1))
''')

case("flow_function_defined_conditionally", "a def inside a branch binds only when run", r'''
def pick(flag):
    if flag:
        def impl():
            return "yes"
    else:
        def impl():
            return "no"
    return impl()


print(pick(True))
print(pick(False))
''')

case("flow_nested_break_only_inner", "break leaves only the innermost loop", r'''
for outer in [1, 2]:
    for inner in [1, 2, 3]:
        if inner == 2:
            break
        print(outer, inner)
print("done")
''')

case("flow_del_removes_binding", "del unbinds a local name", r'''
value = "here"
print(value)
del value
try:
    print(value)
except NameError:
    print("unbound")
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_flow_cases.py", sys.argv))
