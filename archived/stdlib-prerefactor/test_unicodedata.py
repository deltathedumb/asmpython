"""The bundled `unicodedata` answers what the reference implementation does.

The tables are GENERATED from CPython, so what is worth testing is the
algorithms around them -- normalisation is a fixed point over a recursive
mapping followed by a stable reorder and a blocked-composition walk, and every
one of those three has a way of being subtly wrong that no single character
shows.

WHERE THE CHECKS ARE AIMED: run boundaries, because an off-by-one in the
binary search is invisible everywhere else; every code point that HAS a
decomposition, because those are the only ones normalisation moves; and the
Hangul block, which is arithmetic rather than table and is therefore the one
part a regenerated table would not catch.

A full sweep of all 1,114,112 code points passes too and takes half a minute,
which is too slow to run on every change -- `_gen_unicodedata.py` is where
that belongs.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

from tests import harness

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_MODULE = (_ROOT / "src" / "asmpython" / "frontends" / "python" / "bundled"
           / "unicodedata.py")


def _load():
    """The bundled module, BY PATH.

    Not by name, and not by putting its directory on `sys.path`: this file has
    to hold two DIFFERENT modules at once, and only a path import guarantees
    which one it got.
    """
    spec = importlib.util.spec_from_file_location("bundled_unicodedata",
                                                  _MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _reference():
    """CPython's own `unicodedata` -- which `sys.path` CAN shadow.

    `unicodedata` is an extension module rather than a built-in, so a
    `unicodedata.py` earlier on the path wins. And one is: the bundled
    directory goes on `sys.path` in `test_bundled_parser.py`, which imports
    `_pylex` and `_pyparse` by bare name from it. Whether that file was
    imported first decides what `import unicodedata` means here.

    THE FAILURE IT WOULD CAUSE IS SILENT AND TOTAL: every comparison below
    would be the bundled module against itself, twenty-five thousand tests
    passing without checking anything. So the directory is taken off the path
    for the length of one import, and the guard afterwards says whether it
    worked.
    """
    saved_path, saved_module = list(sys.path), sys.modules.pop(
        "unicodedata", None)
    try:
        sys.path[:] = [one for one in sys.path
                       if pathlib.Path(one).resolve() != _MODULE.parent]
        import unicodedata as found
        return found
    finally:
        sys.path[:] = saved_path
        if saved_module is not None:
            sys.modules["unicodedata"] = saved_module


mine = _load()
real = _reference()

#: `_tables` is the bundled module's and the reference implementation has no
#: such name, so this is what tells them apart. Checked at import rather than
#: in a test, because a test that runs against the wrong module has already
#: lost -- and this file is worth nothing if the two are one.
assert mine is not real, "the bundled module was compared with itself"
assert hasattr(mine, "_tables"), "the bundled module is not the bundled one"
assert not hasattr(real, "_tables"), "`unicodedata` resolved to the bundled one"

FORMS = ("NFC", "NFD", "NFKC", "NFKD")


def _interesting() -> list[int]:
    """The code points worth asking about.

    Every mapping's own code point and the ones on either side of it, every
    run boundary in the two run tables and the code point before it, and the
    edges of the Hangul block. Roughly twenty thousand, which is a second
    rather than the half-minute a full sweep costs.
    """
    out = set()
    for cp in mine._tables()["canon"]:
        out.update((cp - 1, cp, cp + 1))
    for cp in mine._tables()["compat"]:
        out.update((cp - 1, cp, cp + 1))
    for start in mine._tables()["cat_at"]:
        out.update((start - 1, start, start + 1))
    for start in mine._tables()["ccc_at"]:
        out.update((start - 1, start, start + 1))
    # The Hangul block is arithmetic and not table, so it needs its own
    # points: both ends, both ends of the jamo ranges, and a spread between.
    out.update(range(0xAC00, 0xAC40))
    out.update(range(0xD7A0, 0xD7A4))
    out.update(range(0x1100, 0x1113))
    out.update(range(0x1161, 0x1176))
    out.update(range(0x11A7, 0x11C3))
    for cp in range(0xAC00, 0xD7A4, 271):
        out.add(cp)
    out.update((0, 1, 0x7F, 0x80, 0xFFFF, 0x10000, 0x10FFFF))
    return sorted(cp for cp in out if 0 <= cp <= 0x10FFFF)


POINTS = _interesting()

#: Sequences a single character cannot exercise: a base with several marks in
#: the wrong order, a mark with nothing before it, a blocked composition.
SEQUENCES = [
    "", "hello", "é", "é", "q̣̇", "q̣̇",
    "ṩ", "ṩ", "ṩ",
    "Å", "Å", "Å",
    "á̖b", "́", "́a", "̈́",
    "각", "각", "힣ᆨ",
    "ﬁne", "①", "㎖", "ñee",
    "ཷ", "ཱྀ", "Ω", "ẛ̣",
    # A long run of marks, which is where a non-stable reorder shows.
    "a" + "̴̖̣́̀̇" * 3,
]


class TestAgreesWithCPython:
    @harness.cases("cp", POINTS)
    def test_properties(self, cp):
        ch = chr(cp)
        assert mine.category(ch) == real.category(ch)
        assert mine.combining(ch) == real.combining(ch)
        assert mine.decomposition(ch) == real.decomposition(ch)

    @harness.cases("cp", POINTS)
    def test_normalising_one_character(self, cp):
        ch = chr(cp)
        if real.category(ch) == "Cs":
            return                      # a surrogate is not text
        for form in FORMS:
            assert mine.normalize(form, ch) == real.normalize(form, ch), (
                form, hex(cp))

    @harness.cases("text", SEQUENCES)
    def test_normalising_a_sequence(self, text):
        """WHERE ORDERING AND BLOCKING LIVE. A single character can be right
        in all four forms while a base carrying two marks comes out reordered,
        which is the one thing normalisation exists to settle."""
        for form in FORMS:
            assert mine.normalize(form, text) == real.normalize(form, text), (
                form, ascii(text))

    @harness.cases("text", SEQUENCES)
    def test_is_normalized_agrees(self, text):
        for form in FORMS:
            assert mine.is_normalized(form, text) is (
                text == real.normalize(form, text))

    def test_idempotent(self):
        """NORMALISING TWICE CHANGES NOTHING, which is the property every use
        of it relies on and is not implied by agreeing on one pass."""
        for text in SEQUENCES:
            for form in FORMS:
                once = mine.normalize(form, text)
                assert mine.normalize(form, once) == once, (form, ascii(text))

    def test_version_matches(self):
        """The tables were generated from THIS interpreter, so a version skew
        means they are stale -- and stale tables disagree quietly."""
        assert mine.unidata_version == real.unidata_version

    def test_refuses_an_unknown_form(self):
        try:
            mine.normalize("NFZ", "a")
        except ValueError:
            return
        raise AssertionError("accepted a form that does not exist")

    def test_refuses_more_than_one_character(self):
        """`category("ab")` is a TypeError, not the category of `a`."""
        for call in (mine.category, mine.combining, mine.decomposition):
            try:
                call("ab")
            except TypeError:
                continue
            raise AssertionError(f"{call.__name__} accepted two characters")
