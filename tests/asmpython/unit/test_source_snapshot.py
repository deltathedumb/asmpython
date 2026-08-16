"""The runner measures ONE tree, whatever happens to `src/` while it runs.

A run imports the compiler out of `src/`, compiles a few hundred programs with
it, and takes minutes. Editing `src/` in the middle means some cases were
compiled with the old code and some with the new, and the number describes a
tree that never existed. The defence used to be "do not edit", which turns
every run into a barrier and is easy to forget -- it has already cost one
thrown-away measurement.

So the runner snapshots `src/` first and points itself at the copy. These
tests are about the property that buys: THE COPY DOES NOT CHANGE when the
original does.
"""
from __future__ import annotations

import os
from pathlib import Path

from tests import harness
from tests.harness import snapshot


@harness.fixture
def tree(tmp_path):
    """A miniature repository: `src/asmpython/thing.py` and nothing else."""
    pkg = tmp_path / "src" / "asmpython"
    pkg.mkdir(parents=True)
    (pkg / "thing.py").write_text("VALUE = 'original'\n", encoding="utf-8")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "stale.pyc").write_bytes(b"\x00\x01")
    return tmp_path


def test_a_snapshot_copies_the_tree(tree):
    taken = snapshot.take(tree, "t1")
    assert (taken / "asmpython" / "thing.py").read_text(encoding="utf-8") \
        == "VALUE = 'original'\n"
    snapshot.discard(taken)


def test_editing_the_original_does_not_change_the_snapshot(tree):
    """THE WHOLE POINT. This is what makes editing during a run safe."""
    taken = snapshot.take(tree, "t2")
    (tree / "src" / "asmpython" / "thing.py").write_text(
        "VALUE = 'edited mid-run'\n", encoding="utf-8")
    assert (taken / "asmpython" / "thing.py").read_text(encoding="utf-8") \
        == "VALUE = 'original'\n", (
            "the snapshot followed the original; a run would be measuring a "
            "tree that changed under it")
    snapshot.discard(taken)


def test_stale_bytecode_is_left_behind(tree):
    """A `.pyc` carries the path it was compiled from, so copying stale
    bytecode next to fresh source is how a run executes neither."""
    taken = snapshot.take(tree, "t3")
    assert not (taken / "asmpython" / "__pycache__").exists()
    snapshot.discard(taken)


def test_current_prefers_the_published_snapshot(tree):
    taken = snapshot.take(tree, "t4")
    was = os.environ.get(snapshot.ENV)
    try:
        snapshot.publish(taken)
        assert snapshot.current(tree) == taken
    finally:
        # RESTORED, or every later test in this process compiles against a
        # directory that has been deleted. A fixture that leaks state into
        # `os.environ` is the kind of thing that fails a different test.
        if was is None:
            os.environ.pop(snapshot.ENV, None)
        else:
            os.environ[snapshot.ENV] = was
    snapshot.discard(taken)


def test_current_falls_back_to_src_when_nothing_is_published(tree):
    was = os.environ.pop(snapshot.ENV, None)
    try:
        assert snapshot.current(tree) == tree / "src"
    finally:
        if was is not None:
            os.environ[snapshot.ENV] = was


def test_discard_refuses_to_delete_a_real_source_tree(tree):
    """`discard` takes a path and removes it. Handed `src/` -- through a bug,
    or a `--live-src` run that thought it had a snapshot -- it would delete
    the compiler. It checks what it was given."""
    snapshot.discard(tree / "src")
    assert (tree / "src" / "asmpython" / "thing.py").exists()


def test_a_missing_src_is_not_an_error(tmp_path):
    """A checkout without `src/` still runs, against nothing to snapshot."""
    assert snapshot.take(tmp_path, "t5") == tmp_path / "src"
