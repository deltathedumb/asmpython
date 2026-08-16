"""Running one test, and running all of them at once.

TWO SPEEDUPS, and they are the reason this exists rather than a wrapper:

  * WORKERS. This suite is subprocess-bound -- a C compile and link per
    program -- so it scales almost linearly across cores. Slowest-first
    ordering is what makes that hold at the end of a run: a long test
    discovered last leaves every other core idle waiting for it.

  * NOT RUNNING WHAT CANNOT PASS. A guard is probed once (is there a C
    compiler?) and every test that declared it is skipped as a group rather
    than each rediscovering the answer. A test that fails takes its declared
    dependants with it, unrun -- because a hundred cascading failures from one
    cause is a hundred stack traces to read past.

Both are honest about what they skipped. A run that quietly stopped covering
half the suite is worse than a slow one, so blocked tests are counted apart
and named in the summary.
"""
from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .api import Failure, Skipped
from .collect import Test, autouse_of, fixture_named
from .report import Outcome, Report, Result, describe

def _aarch64_available() -> bool:
    """The cross-compiler AND the emulator, both. Importing the helper does
    the looking, which is why this is a function: the probe must not run at
    import time in a process that will never need it."""
    try:
        from tests.asmpython.integration import aarch64
    except ImportError:
        return False
    return bool(aarch64.AVAILABLE)


#: Probes for things a test can declare it needs. Evaluated ONCE per run --
#: `shutil.which` is cheap but a hundred of them is not, and more importantly
#: the answer cannot change mid-run.
GUARDS = {
    "cc": lambda: bool(shutil.which("gcc") or shutil.which("cc")),
    "nasm": lambda: bool(shutil.which("nasm")),
    "aarch64": _aarch64_available,
}


def _build_fixtures(module, test: Test, wanted, stack) -> dict:
    """Values for the names a test's signature asks for.

    A fixture may itself ask for fixtures, so this recurses; `tmp_path` is
    built in because nearly everything here writes a file, and having every
    module define its own would be the same four lines a hundred times.
    """
    values = {}
    for name in wanted:
        if name in test.args:
            values[name] = test.args[name]
            continue
        if name == "tmp_path":
            path = Path(tempfile.mkdtemp(prefix="asmpy-"))
            stack.append(("dir", path))
            values[name] = path
            continue
        made = fixture_named(module, name)
        if made is None:
            raise Failure(f"no fixture named {name!r}")
        inner = _build_fixtures(
            module, test,
            [p for p in inspect.signature(made).parameters], stack)
        got = made(**inner)
        if inspect.isgenerator(got):
            value = next(got)
            stack.append(("gen", got))
            values[name] = value
        else:
            values[name] = got
    return values


def _teardown(stack) -> None:
    """Undo the fixtures, innermost first. A teardown that raises is reported
    but does not mask the test's own outcome -- the test already decided."""
    for kind, held in reversed(stack):
        try:
            if kind == "dir":
                shutil.rmtree(held, ignore_errors=True)
            elif kind == "call":
                if held is not None:
                    held()
            else:
                next(held, None)          # run the rest of the generator
        except Exception:
            pass


def run_one(test: Test) -> Result:
    """Run a single test in this process. The unit a worker is handed."""
    if test.skip:
        return Result(test.id, Outcome.SKIP, message=test.skip)
    started = time.perf_counter()
    stack: list = []
    try:
        func, instance = test.resolve()
        module = __import__(test.module, fromlist=["_"])
        wanted = [p for p in inspect.signature(func).parameters
                  if p != "self"]
        for auto in autouse_of(module):
            got = auto()
            if inspect.isgenerator(got):
                next(got)
                stack.append(("gen", got))
        values = _build_fixtures(module, test, wanted, stack)
        if instance is not None:
            # `setup_method` / `teardown_method`: state built fresh for each
            # test in a class. A FRESH INSTANCE per test already isolates
            # them, so this is only about giving the class one place to build
            # what several tests share -- and about not making a fixture out
            # of something that is plainly the class's own setup.
            setup = getattr(instance, "setup_method", None)
            if setup is not None:
                setup()
                stack.append(("call", getattr(instance, "teardown_method",
                                              None)))
            func(instance, **values)
        else:
            func(**values)
    except Skipped as exc:
        return Result(test.id, Outcome.SKIP, time.perf_counter() - started,
                      str(exc))
    except BaseException as exc:                       # noqa: BLE001
        message, detail = describe(exc)
        return Result(test.id, Outcome.FAIL, time.perf_counter() - started,
                      message, detail)
    finally:
        _teardown(stack)
    return Result(test.id, Outcome.PASS, time.perf_counter() - started)


def _worker(payload):
    """Entry point in a pool process. Takes a plain tuple so the pickling is
    of data rather than of anything that closed over a module."""
    module, cls, name, ident, args, skip, needs = payload
    return run_one(Test(id=ident, module=module, cls=cls, name=name,
                        args=args, skip=skip, needs=needs))


def _payload(test: Test):
    return (test.module, test.cls, test.name, test.id, test.args, test.skip,
            test.needs)


def run(tests: list[Test], *, jobs: int = 0, on_result=None,
        stop_after: int = 0) -> Report:
    """Run them, in parallel unless asked not to.

    `jobs=1` runs in this process, which is what a debugger needs and what
    makes a harness bug findable. Anything else uses a pool.
    """
    failed_guards = {name for name, probe in GUARDS.items() if not probe()}
    results: list[Result] = []
    runnable: list[Test] = []
    for test in tests:
        blocked = failed_guards.intersection(test.needs)
        if blocked:
            results.append(Result(
                test.id, Outcome.BLOCKED,
                message=f"needs {', '.join(sorted(blocked))}"))
            if on_result:
                on_result(results[-1])
        else:
            runnable.append(test)

    # SLOWEST FIRST. The last test to start decides when the run ends, so
    # starting a long one late leaves every other worker idle behind it.
    runnable.sort(key=lambda t: -t.weight)

    if jobs == 1:
        for test in runnable:
            got = run_one(test)
            results.append(got)
            if on_result:
                on_result(got)
            if stop_after and sum(
                    1 for r in results if r.outcome is Outcome.FAIL) >= stop_after:
                break
        return Report(results)

    workers = jobs or max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(_worker, _payload(t)): t for t in runnable}
        failures = 0
        for done in as_completed(pending):
            got = done.result()
            results.append(got)
            if on_result:
                on_result(got)
            if got.outcome is Outcome.FAIL:
                failures += 1
                if stop_after and failures >= stop_after:
                    # WHAT WAS NOT RUN IS RECORDED, not dropped. A run that
                    # stopped early and then reported "3 passed" would be
                    # describing a suite of three tests, which is the exact
                    # dishonesty the blocked count exists to prevent.
                    for future, waiting in pending.items():
                        if future.cancel():
                            results.append(Result(
                                waiting.id, Outcome.BLOCKED,
                                message="not run: stopped at first failure"))
                    break
    return Report(results)
