"""The project's own test runner.

WHY NOT PYTEST. This suite is not a general one. Nearly every test in it says
"compile this program and compare it against CPython", four ways, and the
thing that matters when one fails is WHICH PATH disagreed and WHERE the two
outputs diverged. A general runner shows an assertion; this one is free to
show the diff.

The rest is arithmetic. The suite spends its time in subprocesses -- a C
compile and link per program -- so it parallelises almost perfectly, and
running it on every core turns a quarter-hour into a couple of minutes. That
is the difference between a suite you run before every commit and one you run
before a release.

The API is deliberately small, and it is the whole of what the tests use:

    @cases("name", [...])        one test per value, named by it
    with raises(TypeError):      the block must raise
    @skip_if(cond, "why")        not applicable here
    @fixture                     a value built per test, torn down after
    fail("...") / skip("...")    decide from inside a test

Collection is by NAME, as every runner does it: `test_*.py` files, `Test*`
classes, `test_*` functions. A test's parameters are fixture names, and
`tmp_path` is built in because almost everything here writes a file.
"""
from __future__ import annotations

from .api import (cases, fail, fixture, needs, param, raises, skip,
                  skip_if)
from .collect import Test, collect
from .report import Outcome, Report
from .run import run

__all__ = [
    "Outcome", "Report", "Test", "cases", "collect", "fail", "fixture",
    "needs", "param", "raises", "run", "skip", "skip_if",
]
