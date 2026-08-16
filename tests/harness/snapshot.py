"""A frozen copy of the compiler, so a run measures one tree.

THE PROBLEM THIS SOLVES. A run imports `asmpython` out of `src/`, compiles a
few hundred programs with it, and takes minutes. Editing `src/` while that
happens means some cases were compiled with the old code and some with the
new, and the result describes a tree that never existed. The only defence was
not to edit -- which turns every run into a barrier, and which is easy to
forget: it has already cost one thrown-away measurement here.

So the runner takes a SNAPSHOT at startup and points itself at that. `src/` is
then free to change under it: the copy is what the run measures, start to
finish, whatever anyone does to the original meanwhile.

WHERE THE WORKERS GET IT. `multiprocessing` sends the parent's `sys.path` to
each spawned child, so redirecting the parent before the pool exists is enough
for every worker. Tests that reach for the tree by PATH -- the CLI and plugin
ones, which start subprocesses -- read `ASMPYTHON_SRC` instead of building
`root/"src"` themselves.

WHAT IS NOT SNAPSHOT. `tests/` itself. A test file is read once at collection,
before any of this matters, and copying the suite would break every fixture
that locates a data file relative to its own `__file__`.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

#: Where the copies live. One directory, hidden by the leading dot, holding
#: one subdirectory per run so two concurrent runs cannot tread on each other.
CACHE_DIR = ".harness-src"

#: The environment variable a worker -- or a test that starts a subprocess --
#: reads to find the tree this run is measuring.
ENV = "ASMPYTHON_SRC"


def _ignore(_dir, names):
    """Skip what only slows the copy down or would be stale in it.

    `__pycache__` is the important one: a `.pyc` carries the path it was
    compiled from, and copying stale bytecode next to fresh source is how a
    run ends up executing neither.
    """
    return [n for n in names if n in ("__pycache__", ".mypy_cache")
            or n.endswith(".pyc")]


def take(root: Path, token: str) -> Path:
    """Copy `root/src` into the cache and answer the copy's path.

    `token` names this run's directory. Answers the ORIGINAL `src` unchanged
    when there is nothing to copy, so a checkout without one still runs.
    """
    src = root / "src"
    if not src.is_dir():
        return src
    into = root / CACHE_DIR / token
    if into.exists():
        shutil.rmtree(into, ignore_errors=True)
    into.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, into, ignore=_ignore)
    return into


def publish(path: Path) -> None:
    """Make `path` the tree this process and its children compile with."""
    os.environ[ENV] = str(path)


def current(root: Path) -> Path:
    """The tree to import from: this run's snapshot, or `src` if there is none.

    Read by `collect`, and by the few tests that need the path rather than the
    import -- so a subprocess they start compiles with the same code the rest
    of the run did.
    """
    got = os.environ.get(ENV)
    if got and Path(got).is_dir():
        return Path(got)
    return root / "src"


def discard(path: Path) -> None:
    """Remove a snapshot. Quiet on failure: a leftover copy is untidy, and an
    error here would fail a run that has already finished its work."""
    try:
        if path.name != "src" and CACHE_DIR in path.parts:
            shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
