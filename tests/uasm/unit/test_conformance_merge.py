"""Merge mode packs many cases into one program without changing what they mean.

The mode exists to see what a case-per-process run cannot: state the runtime
keeps for the length of ONE program, and names two cases both bind. Its whole
value rests on one promise -- that a case merged into a batch means exactly
what it meant alone -- and this pins that promise from both sides:

  * NAMES. Two cases may share a batch only if their module-level bindings are
    disjoint, because the alternative is renaming what a case declares, and
    `cases/` is the oracle.
  * OUTPUT. A batch is compared segment by segment, not as one concatenation.
    The difference is not cosmetic: the harness rstrips a case's output before
    comparing, so a case ending in `print("")` has a trailing blank line its
    `# expect:` block cannot hold. Harmless alone, and wrong the moment another
    case's output follows -- it failed three real cases the first time this
    mode ran, none of which had anything wrong with them.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "conformance"))
try:
    import harness as conf
    from shims import cpython as cpython_shim
finally:
    if sys.path and sys.path[0] == str(_ROOT / "conformance"):
        del sys.path[0]


def _case(tmp: pathlib.Path, name: str, source: str, expect: str):
    """A Case backed by a real file, because `run_batch` reads the file."""
    path = tmp / (name + ".py")
    path.write_text(source, encoding="utf-8")
    return conf.Case(id=name, path=path, tier="spec", ref="", expect=expect)


def _names(source: str) -> set:
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "one.py"
        path.write_text(source, encoding="utf-8")
        return conf._module_names(path)


def _pack(sources: dict, size: int):
    tmp = pathlib.Path(tempfile.mkdtemp())
    cases = [_case(tmp, name, src, "x") for name, src in sources.items()]
    return conf._batches(cases, size)


def _run(batch: list):
    return conf.run_batch(batch, cpython_shim, 60, pathlib.Path(
        tempfile.mkdtemp()))


class TestModuleNames:
    """What two cases may not share."""

    def test_finds_the_obvious_bindings(self):
        assert _names("x = 1\ndef f(): pass\nclass C: pass\n") == {"x", "f",
                                                                   "C"}

    def test_finds_the_bindings_that_do_not_look_like_assignments(self):
        """A `for` target, an `import ... as` and a walrus all bind, and a run
        that missed any of them would merge two cases that clobber each
        other -- the exact failure this mode must never invent."""
        found = _names("for i in range(3): pass\n"
                       "import os.path as p\n"
                       "from sys import argv\n"
                       "with open(__file__) as fh: pass\n"
                       "if (w := 1): pass\n")
        for name in ("i", "p", "argv", "fh", "w"):
            assert name in found, name

    def test_finds_a_global_declared_inside_a_function(self):
        """`global` binds at module level however deep it is, so it counts even
        though nothing at module level looks like a store."""
        assert "counter" in _names("def f():\n"
                                   "    global counter\n"
                                   "    counter = 1\n")

    def test_a_case_the_oracle_cannot_parse_yields_nothing(self):
        """Read by `_batches` as "never merge this". A case whose names cannot
        be known must not be assumed to have none."""
        assert _names("def (:\n") == set()


class TestObservesNamespace:
    """Which cases are free to share a name, and which are not."""

    def _asks(self, source: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "one.py"
            path.write_text(source, encoding="utf-8")
            return conf._observes_namespace(path)

    def test_ordinary_code_is_free(self):
        """It binds every name it reads -- if it did not, CPython would raise
        NameError and the case would not pass alone."""
        assert not self._asks("x = 1\nfor i in range(3): x += i\nprint(x)\n")

    def test_asking_the_namespace_is_not(self):
        for source in ("print(globals())\n", "print(locals())\n",
                       "print(vars())\n", "print(dir())\n"):
            assert self._asks(source), source

    def test_probing_for_an_unbound_name_is_not(self):
        """The one shape the free-to-merge argument does not cover: a case
        whose whole point is whether a name is bound."""
        assert self._asks("try:\n    print(nope)\n"
                          "except NameError:\n    print('unbound')\n")

    def test_del_is_not(self):
        """`del` is how a case makes a name unbound on purpose."""
        assert self._asks("x = 1\ndel x\n")

    def test_a_case_the_oracle_cannot_parse_is_not(self):
        """Unknown, so treated as observing rather than assumed free."""
        assert self._asks("def (:\n")


class TestBatches:
    """Packing: two pools, bounded, and lossless."""

    def test_free_cases_pack_regardless_of_the_names_they_share(self):
        """Both bind `x`, and it does not matter: the second binds it before
        it reads it, exactly as it did alone."""
        batches, alone = _pack({"a": "x = 1\nprint(x)\n",
                                "b": "x = 2\nprint(x)\n"}, 8)
        assert alone == []
        assert [len(b) for b in batches] == [2]

    def test_a_case_that_asks_never_shares_with_one_that_does_not(self):
        """A free case in an observer's batch is exactly the binding the
        observer would see."""
        batches, _ = _pack({"a": "x = 1\nprint(x)\n",
                            "b": "y = 2\nprint(sorted(globals())[:0], y)\n"},
                           8)
        assert sorted(len(b) for b in batches) == [1, 1]

    def test_two_cases_that_ask_share_only_when_their_names_are_disjoint(self):
        batches, _ = _pack({"a": "x = 1\nprint(len(globals()) > 0)\n",
                            "b": "x = 2\nprint(len(globals()) > 0)\n",
                            "c": "y = 3\nprint(len(globals()) > 0)\n"}, 8)
        assert sorted((len(b) for b in batches), reverse=True) == [2, 1]

    def test_no_batch_exceeds_the_requested_size(self):
        sources = {("c%d" % i): ("v%d = %d\nprint(v%d)\n" % (i, i, i))
                   for i in range(10)}
        batches, alone = _pack(sources, 3)
        assert alone == []
        assert max(len(b) for b in batches) <= 3

    def test_every_case_is_accounted_for(self):
        """A mode that dropped what it could not batch would report a faster
        run over fewer cases."""
        sources = {("c%d" % i): ("v%d = %d\nprint(v%d)\n" % (i, i, i))
                   for i in range(10)}
        sources["bad"] = "def (:\n"
        batches, alone = _pack(sources, 3)
        seen = [c.id for b in batches for c in b] + [c.id for c in alone]
        assert len(seen) == 11, seen
        assert len(set(seen)) == 11, seen
        assert "bad" in [c.id for c in alone]


class TestRunBatch:
    """Running: a merged verdict means what a solo one means."""

    def test_a_passing_batch_passes_every_case(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        batch = [_case(tmp, "a", "print(1)\n", "1"),
                 _case(tmp, "b", "print(2)\n", "2")]
        assert [r.status for r in _run(batch)] == ["PASS", "PASS"]

    def test_a_trailing_blank_line_does_not_fail_the_next_case(self):
        """THE REGRESSION. `text/str/case-conversions` ends with `print("")`;
        its expect block cannot hold that blank line because the harness
        rstrips. Compared as one concatenation the blank shifts everything
        after it and the whole batch fails -- three cases reported as
        interaction bugs, none of which had anything wrong with them."""
        tmp = pathlib.Path(tempfile.mkdtemp())
        batch = [_case(tmp, "a", 'print("hi")\nprint("")\n', "hi"),
                 _case(tmp, "b", "print(2)\n", "2")]
        assert [r.status for r in _run(batch)] == ["PASS", "PASS"]

    def test_only_the_case_that_differs_is_returned_unsettled(self):
        """A batch that reported all-or-nothing would send every neighbour of
        a failure back for a solo re-run, and the mode's saving with it."""
        tmp = pathlib.Path(tempfile.mkdtemp())
        batch = [_case(tmp, "a", "print(1)\n", "1"),
                 _case(tmp, "b", "print(2)\n", "999"),
                 _case(tmp, "c", "print(3)\n", "3")]
        assert [r.status for r in _run(batch)] == ["PASS", "BATCH", "PASS"]

    def test_a_crash_settles_nothing(self):
        """Everything after a crash never ran, and everything before it shares
        a process with one -- so the batch is not a verdict on any of them."""
        tmp = pathlib.Path(tempfile.mkdtemp())
        batch = [_case(tmp, "a", "print(1)\n", "1"),
                 _case(tmp, "b", "raise SystemExit(3)\n", "")]
        assert [r.status for r in _run(batch)] == ["BATCH", "BATCH"]
