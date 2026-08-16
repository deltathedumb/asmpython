# The test runner

```sh
python -m tests.harness                  # the whole suite, on every core
python -m tests.harness -k with_stmt     # only tests whose id contains this
python -m tests.harness -j 1             # in this process, for a debugger
python -m tests.harness -x               # stop at the first failure
python -m tests.harness --cached         # skip what already passed unchanged
python -m tests.harness --slowest 10     # where the time went
```

## Why this and not a general runner

Two reasons, and both are about this suite specifically.

**The failures are diffs.** Nearly every test here says "compile this program
and compare it against CPython", four ways. What matters when one fails is
which path disagreed and where the outputs diverged — so a bare `assert a == b`
is reconstructed from the traceback and the first differing line is printed:

```
FAILED tests.asmpython.integration.test_dynamic_python::...[with_statement]
  assert ran.stdout.split("\n")[:-1] == cpython(src)
  first difference at index 2:
    got:  'exit 2 None'
    want: 'exit 2 ValueError'
```

No assert rewriting is involved. The frame is still alive when the exception
is caught, the source line says which names to read, and the values are still
bound to them. Nothing is re-executed — re-running the expression could have
side effects, and could just as easily succeed the second time.

**The suite is subprocess-bound.** A C compile and link per program means it
parallelises almost linearly. That is the difference between a suite you run
before every commit and one you run before a release.

## Making it fast

| | |
|---|---|
| **Workers** | Every core by default. `-j 1` for one process. |
| **Slowest first** | The last test to start decides when the run ends, so a long one discovered late leaves the other cores idle. Timings come from the last run. |
| **Guards** | `@harness.needs("cc")` is probed **once** per run, not per test, and everything declaring it is reported **blocked** as a group. `cc`, `nasm`, `aarch64`. |
| **Caching** | `--cached` skips tests that passed and whose inputs have not changed. |
| **Stop early** | `-x` cancels the remaining work at the first failure. |

The cache hashes **content**, not mtimes: a branch switch rewrites mtimes
without changing anything, and a cache that invalidates on those is one nobody
benefits from. It is coarse — touching any file under `src/` invalidates the
whole suite — and that is deliberate. A cache that sometimes skips a test that
would now fail is worse than no cache.

Skips are **counted apart** from passes, and blocked tests apart from skips. A
run reporting "1150 passed" having executed twelve of them is lying, so the
summary says how many were taken on trust.

## The API

That is the whole of it, and it is the whole of what the tests use.

```python
from tests import harness

@harness.cases("backend", ["c", "x86-64"])       # one test per value
def test_it_compiles(backend): ...

@harness.cases("name", sorted(PROGRAMS))         # on a class: distributes
class TestEveryPathAgrees: ...                   # over every test in it

with harness.raises(TypeError, match="not callable"):
    ...

@harness.needs("cc")                              # probed once per run
def test_the_c_backend(): ...                     # blocked, not skipped

@harness.skip_if(sys.platform == "win32", "no")  # the reason is required
def test_posix_only(): ...

@harness.fixture                                  # a value per test
def program(tmp_path): ...                        # fixtures may nest

@harness.fixture(autouse=True)                    # runs whether asked or not
def _no_leaks():
    yield                                         # anything after is teardown
    reset()

harness.fail("...")     harness.skip("...")       # decide from inside a test
```

`tmp_path` is built in — nearly everything here writes a file, and every
module defining its own would be the same four lines a hundred times.
`setup_method` / `teardown_method` on a class work as they read.

Collection is by name: `test_*.py`, `Test*`, `test_*`. A naming rule needs no
registration step, and a registration step is a thing to forget.

A module that fails to import stops the run rather than quietly contributing
no tests. A suite that silently shrinks is worse than one that fails.
