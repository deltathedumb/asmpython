"""The interpreter must be able to run what the compiler emits.

The project's central invariant is that CPython, the reference interpreter and
every backend agree on the same program. A runtime symbol with no host binding
breaks that in the quietest possible way: the compiled binary is right, the
interpreter traps, and nothing notices until someone runs `asmpython run` on a
program that happens to use it.

It happened. `apy_sorted`, then every string method, then set algebra were
added to `link/objects.py` and reached compiled programs immediately, while
`asmpython run` trapped on all of them -- 118 of 177 symbols were unbound
before anyone looked.
"""
from __future__ import annotations

import re

from asmpython.ir.objects_host import _TABLE
from asmpython.link.objects import OBJECT_NAMES, OBJECTS_C

#: Symbols with no host binding yet. A RATCHET, not a permission: the test
#: fails when a symbol is added to the runtime without one, and equally when
#: one is bound and left in this list. It is meant to shrink to empty.
#:
#: EMPTY, and the point is to keep it that way: every symbol the runtime
#: defines is reachable from the interpreter, so `asmpython run` and a compiled
#: binary answer the same question with the same code.
UNBOUND = set()


def test_every_runtime_symbol_has_a_host_binding():
    unbound = set(OBJECT_NAMES) - set(_TABLE)
    added = sorted(unbound - UNBOUND)
    assert not added, (
        "runtime symbols with no binding in ir/objects_host.py:\n  "
        + "\n  ".join(added)
        + "\n\nA compiled program can call these and `asmpython run` cannot. "
          "Add a binding, or add the name to UNBOUND with the feature it "
          "belongs to.")


def test_every_exported_symbol_has_a_host_binding():
    """The same invariant as above, but read off THE C ITSELF.

    The ratchet above starts from `OBJECT_NAMES`, which is hand-written and
    holds 188 of the 298 exported symbols. A symbol absent from that list is
    invisible to it -- so four `typing` entry points went in with no
    interpreter binding and every test here still passed; only running a
    program through `asmpython run` found it.

    The other 110 absentees turned out to be bound already, so the list being
    a subset is not itself a bug and this does not demand they be added. What
    it demands is the thing that actually breaks: an APY_API symbol a
    compiled program can call and the interpreter cannot.
    """
    exported = set(re.findall(r"^APY_API\s+apy_value\s+(apy_\w+)\s*\(",
                              OBJECTS_C, re.M))
    missing = sorted(exported - set(_TABLE) - UNBOUND)
    assert not missing, (
        "exported by link/objects.py with no binding in ir/objects_host.py:\n  "
        + "\n  ".join(missing)
        + "\n\nA compiled program can call these and `asmpython run` cannot.")


def test_the_unbound_list_does_not_go_stale():
    unbound = set(OBJECT_NAMES) - set(_TABLE)
    now_bound = sorted(UNBOUND - unbound)
    assert not now_bound, (
        "these are listed as unbound but now have a binding; remove them "
        "from UNBOUND:\n  " + "\n  ".join(now_bound))


def test_the_unbound_list_names_real_symbols():
    """A typo in UNBOUND would silently excuse a symbol that does not exist,
    and stop excusing the one that does."""
    unknown = sorted(UNBOUND - set(OBJECT_NAMES))
    assert not unknown, (
        "UNBOUND names symbols the runtime does not define:\n  "
        + "\n  ".join(unknown))
