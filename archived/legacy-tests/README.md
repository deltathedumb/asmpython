# The 3.13 test suite

**These do not run against this tree, and that is why they are here.**

They test `archived/legacy/asmpython` -- the pre-rewrite compiler -- and they
say so in their imports:

```python
from asmpython._backends import get_backend      # the legacy package
import pytest                                     # the legacy test runner
```

and in how they invoke the compiler:

```
python -m asmpython <file>          legacy: the path IS the command
python -m asmpython build <file>    3.14: the path is an argument to `build`
```

## Why they were moved rather than deleted

They are the accumulated record of what the 3.13 compiler was made to do, and
some of it -- the AArch64 codegen cases especially -- is knowledge the 3.14
tree does not yet have anywhere else. Deleting them throws that away; leaving
them in `tests/` made them look like a suite.

## Why leaving them in `tests/` was actively harmful

`tests/runner.py` walks 1,935 cases, runs each through the legacy CLI, and
reports:

    0/1932 passed

Every one of those failures says `invalid choice: '<path>'` -- the 3.14 CLI
declining a command that does not exist any more. It is not a signal about the
compiler at all, and it reads exactly like one. It was misread as a regression
during the runtime port, by someone who then had to go and find out why.

The living suite is `tests/asmpython/`, run with `python -m tests.harness`:
44 files, ~29,500 tests, and no pytest.

## What stayed behind

`tests/jvm_differential.py` uses the 3.14 CLI (`asmpython build ...`) and is a
tool for the current JVM backend, so it is still in `tests/`.

## If you want any of this back

Porting a case means rewriting its invocation for `asmpython build` and its
assertions for `tests/harness` rather than pytest. The corpus cases under
`cases/` are the cheapest: each is a Python file with a `# expect:` block, and
the format the conformance suite uses is close enough to convert mechanically.
