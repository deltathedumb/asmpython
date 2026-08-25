"""The exception hierarchy exists twice and must say the same thing both times.

THE C KEEPS `APY_EXC_TREE`, a table of string pointers, because that is what
the runtime uses when nothing is ported. `runtime/errstate.py` keeps the same
pairs packed into a `rodata` blob, because a table of POINTERS is the one thing
`rodata` cannot hold -- it hands back bytes, and a pointer needs the linker to
fill it in.

So the SHAPE differs and the CONTENT must not. Without this test, adding an
exception to one copy would leave `except` quietly disagreeing with itself
depending on which object runtime a program was built with, and nothing else in
the suite compares the two.

IT IS ITS OWN FILE rather than another class in `test_ported_int.py`, which
guards the LAYOUT constants by asking a C compiler. This asks no compiler and
reads two pieces of source; sharing a file would mean sharing a fixture that
builds a probe for no reason.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[3] / "src")

#: One `{"Name", "Parent"}` row of the C's table.
_C_ROW = re.compile(r'\{\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\}')

#: One `b"..."` piece of the IR's blob. The pieces are adjacent literals that
#: Python joins, so every one of them has to be collected and concatenated --
#: reading only the first would compare one entry against sixty-five.
#: One quoted name in the C's `known[]` table.
_C_NAME = re.compile(r'"([^"]+)"')

_IR_PIECE = re.compile(r'(b"(?:[^"\\]|\\.)*")')


def _c_pairs() -> list[tuple[str, str]]:
    sys.path.insert(0, SRC)
    try:
        from asmpython.objects.csource import OBJECTS_C
    finally:
        del sys.path[0]
    start = OBJECTS_C.index("static const char *const APY_EXC_TREE")
    return _C_ROW.findall(OBJECTS_C[start:OBJECTS_C.index("};", start)])


def _packed(fn: str) -> list[str]:
    """The NUL-separated names one `rodata` blob in `errstate.py` holds.

    SHARED BY BOTH CHECKS BELOW, because the exception tree and the bundled
    module list are the same shape: names, each terminated, then one more
    terminator. The tree pairs them up afterwards and the module list does
    not, which is the only difference between the two.
    """
    source = (Path(SRC) / "asmpython" / "runtime"
              / "errstate.py").read_text(encoding="utf-8")
    start = source.index("def " + fn + "()")
    end = source.index("def ", source.index("return rodata(", start))
    blob = b"".join(ast.literal_eval(piece)
                    for piece in _IR_PIECE.findall(source[start:end]))
    parts = blob.split(b"\0")
    assert parts[-2:] == [b"", b""], (
        fn + " must end with an empty name, which is what the walk stops at")
    return [p.decode() for p in parts[:-2]]


def _c_modules() -> list[str]:
    sys.path.insert(0, SRC)
    try:
        from asmpython.objects.csource import OBJECTS_C
    finally:
        del sys.path[0]
    start = OBJECTS_C.index("static const char *known[] = {")
    return _C_NAME.findall(
        OBJECTS_C[start:OBJECTS_C.index("0};", start)])


def _ir_pairs() -> list[tuple[str, str]]:
    source = (Path(SRC) / "asmpython" / "runtime"
              / "errstate.py").read_text(encoding="utf-8")
    start = source.index("def apy_exc_tree()")
    end = source.index("def ", source.index("return rodata(", start))
    blob = b"".join(ast.literal_eval(piece)
                    for piece in _IR_PIECE.findall(source[start:end]))
    # THE TRAILING EMPTY NAME IS THE TERMINATOR the walk stops at, and
    # splitting on NUL turns it into two empty pieces at the end: one for the
    # terminator itself and one after the final separator.
    parts = blob.split(b"\0")
    assert parts[-2:] == [b"", b""], (
        "the packed tree must end with an empty name, which is what "
        "`apy_exc_parent_of` stops walking at")
    names = [p.decode() for p in parts[:-2]]
    assert len(names) % 2 == 0, "the blob must hold whole name/parent pairs"
    return [(names[i], names[i + 1]) for i in range(0, len(names), 2)]


class TestTheTreeIsTheCs:
    def test_the_two_copies_agree(self):
        c, ir = _c_pairs(), _ir_pairs()
        assert c == ir, (
            "the C's APY_EXC_TREE and the packed blob in "
            "runtime/errstate.py disagree.\n"
            f"  only in the C:  {[p for p in c if p not in ir]}\n"
            f"  only in the IR: {[p for p in ir if p not in c]}")

    def test_every_parent_is_itself_known(self):
        """A parent naming nothing is a chain that ends in the wrong place.

        `apy_exc_parent_of` walks up until it finds no parent, and `except`
        stops there -- so a typo in a parent name does not fail, it silently
        makes a class catchable by less than it should be. Only
        `BaseException` may be unnamed, because it is the root.
        """
        pairs = _ir_pairs()
        known = {name for name, _ in pairs} | {"BaseException"}
        orphans = sorted({parent for _, parent in pairs} - known)
        assert not orphans, f"parents nothing declares: {orphans}"


class TestTheModuleListIsTheCs:
    """The bundled module names exist twice too, and for the same reason.

    A NAME IN ONE COPY AND NOT THE OTHER IS NOT A CRASH, which is what makes
    this worth a test: `__import__("math")` would report `No module named
    'math'` from one arrangement and an ImportError from the other, and both
    look like plausible answers.
    """

    def test_the_two_copies_agree(self):
        c, ir = _c_modules(), _packed("apy_known_modules")
        assert c == ir, (
            "the C's `known[]` and the packed blob in runtime/errstate.py "
            "disagree.\n"
            f"  only in the C:  {[n for n in c if n not in ir]}\n"
            f"  only in the IR: {[n for n in ir if n not in c]}")
