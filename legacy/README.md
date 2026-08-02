# The legacy compiler

This is asmpython as it was before the rewrite: the NASM-emitting compiler,
its runtime, its backends and its stdlib. It is kept for the code generation
in it, which is real and hard-won, and it is **not maintained** — see the
project constraints.

## Why it is in a subdirectory

The rewrite in `src/asmpython/` is also called `asmpython`, and two packages
cannot share one import name. Whichever directory comes first on `sys.path`
wins and the other becomes invisible — silently, since both answer to `import
asmpython`.

So the new compiler owns the name at `src/asmpython/`, and this one moved down
a level. Nothing inside it changed: the move is a directory rename, and every
import in here is either relative or absolute-to-itself, both of which still
resolve once `legacy/` is the path entry.

## Using it

```bash
PYTHONPATH=legacy python -m asmpython._compiler --help
PYTHONPATH=legacy python -m pytest tests/test_something.py
```

`asmpython.sh` and `asmpython.bat` at the repo root already do this — they are
wrappers for THIS compiler, and were updated to point here.

You cannot import both trees in one process. That is inherent to them sharing
a name, not something a path trick can work around.

## What still refers to it

`tests/*.py` (the loose files; `tests/asmpython/` belongs to the new tree),
`selfhost/`, `examples/`, `conformance/` and `archived/` all import
`asmpython` expecting this one. They need `PYTHONPATH=legacy`.
