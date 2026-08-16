"""What a test file imports: the decorators and the two control functions.

Everything here either ATTACHES METADATA to a function or raises one of the
outcome signals. Nothing runs a test -- `run.py` does that -- so a test file
can be imported by anything without a runner being involved, which is what
keeps `python -c "import tests.asmpython.unit.test_ir"` a sane thing to do.
"""
from __future__ import annotations

import re
from typing import Any, Callable

#: Attribute names the collector reads back off a decorated function. Prefixed
#: so they cannot collide with anything a test defines, and named in one place
#: so the collector and the decorators cannot drift about the spelling.
CASES = "__harness_cases__"
SKIP = "__harness_skip__"
FIXTURE = "__harness_fixture__"
AUTOUSE = "__harness_autouse__"
NEEDS = "__harness_needs__"


class Failure(AssertionError):
    """A test decided it had failed. Carries no more than its message."""


class Skipped(Exception):
    """A test decided it did not apply. Not a failure and not a pass."""


def fail(message: str = "") -> None:
    """Fail from inside a test, where an assertion would not read as well."""
    raise Failure(message)


def skip(reason: str = "") -> None:
    """Skip from inside a test, once something only discoverable there is
    known -- a toolchain that is installed but too old, say."""
    raise Skipped(reason)


class param:
    """One case of a `cases(...)` list, with a name of its own.

    Only needed when the values do not read well as an id -- a long source
    string, a tuple of flags. `cases` names an unadorned value by its repr,
    which is right for a number or a short string and unreadable for a program.
    """

    __slots__ = ("values", "id")

    def __init__(self, *values: Any, id: str | None = None) -> None:
        self.values = values
        self.id = id


def cases(names: str, values) -> Callable:
    """One test per value.

    `names` is a comma-separated parameter list, as the test's own signature
    spells it, and each entry of `values` is a tuple matching it -- or a bare
    value when there is one parameter. Applied to a CLASS it distributes over
    every test in it, which is how a whole class runs against each of a corpus
    of programs.

    Stacked decorators multiply, and the outermost varies slowest: two
    `cases` of three and two values are six tests, named for both.
    """
    wanted = [n.strip() for n in names.split(",") if n.strip()]

    def attach(target):
        existing = list(getattr(target, CASES, ()))
        setattr(target, CASES, existing + [(wanted, list(values))])
        return target

    return attach


def skip_if(condition: bool, reason: str) -> Callable:
    """Skip when `condition` holds. The reason is REQUIRED: a skipped test is
    invisible, and one nobody can explain is a test nobody will restore."""
    def attach(target):
        if condition:
            setattr(target, SKIP, reason)
        return target

    return attach


def needs(*guards: str) -> Callable:
    """Declare a tool this test cannot run without.

    The guard is probed ONCE per run rather than per test, and everything
    declaring it is reported as BLOCKED as a group -- distinct from a skip,
    because "could not run" and "does not apply" are different facts and a
    suite that quietly stopped covering a backend should say so.

    Known guards live in `run.GUARDS`. An unknown one is not an error here:
    the runner reports it, which is better than a decorator that fails at
    import time and takes the whole module with it.
    """
    def attach(target):
        existing = tuple(getattr(target, NEEDS, ()))
        setattr(target, NEEDS, existing + guards)
        return target

    return attach


def fixture(func=None, *, autouse: bool = False):
    """A value built for each test that asks for it, by name.

    A fixture that YIELDS is torn down after the test, which is what a
    temporary server or a patched global needs. One that returns is simply a
    value. `autouse` runs it for every test in its module whether or not
    anything names it -- for a fixture whose whole job is to undo something.
    """
    def attach(f):
        setattr(f, FIXTURE, True)
        setattr(f, AUTOUSE, autouse)
        return f

    return attach(func) if func is not None else attach


class raises:
    """Assert that the block raises `expected`, and let it be inspected.

    `match` is a regex searched in the exception's text. It exists because
    "raised ValueError" is usually not the claim being made -- the claim is
    that it raised the RIGHT ValueError, and a test that does not check the
    message passes when the code fails for an unrelated reason.
    """

    __slots__ = ("expected", "match", "value")

    def __init__(self, expected, match: str | None = None) -> None:
        self.expected = expected
        self.match = match
        #: The exception that was raised, for a test that wants to look at it.
        self.value: BaseException | None = None

    def __enter__(self) -> "raises":
        return self

    def __exit__(self, kind, value, tb) -> bool:
        if kind is None:
            name = getattr(self.expected, "__name__", str(self.expected))
            raise Failure(f"expected {name} but nothing was raised")
        if not issubclass(kind, self.expected):
            return False           # a different exception: let it propagate
        self.value = value
        if self.match is not None and not re.search(self.match, str(value)):
            raise Failure(
                f"{kind.__name__} was raised but its message does not match "
                f"{self.match!r}: {str(value)!r}")
        return True
