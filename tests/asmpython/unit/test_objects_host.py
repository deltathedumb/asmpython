"""The interpreter must be able to run what the compiler emits.

The project's central invariant is that CPython, the reference interpreter and
every backend agree on the same program. A runtime symbol with no host binding
breaks that in the quietest possible way: the compiled binary is right, the
interpreter traps, and nothing notices until someone runs `asmpython run` on a
program that happens to use it.

It happened. `apy_sorted`, then every string method, then set algebra were
added to `objects/csource.py` and reached compiled programs immediately, while
`asmpython run` trapped on all of them -- 118 of 177 symbols were unbound
before anyone looked.
"""
from __future__ import annotations

import re

from asmpython.ir.objects_host import _TABLE
from asmpython.objects.csource import OBJECT_NAMES, OBJECTS_C

#: Symbols with no host binding yet. A RATCHET, not a permission: the test
#: fails when a symbol is added to the runtime without one, and equally when
#: one is bound and left in this list. It is meant to shrink to empty.
#:
#: EMPTY, and the point is to keep it that way: every symbol the runtime
#: defines is reachable from the interpreter, so `asmpython run` and a compiled
#: binary answer the same question with the same code.
UNBOUND = set()

#: Exported helpers the host implements by owning their CALLER whole.
#:
#: A SEPARATE LIST FROM `UNBOUND` ON PURPOSE, so that one stays empty and goes
#: on meaning what it says. These are different in kind: each exists only
#: because a C `static` needs an exported half before IR can replace it, and
#: the only thing that ever calls one is the runtime itself. On the interpreter
#: path no runtime IR runs at all -- the host claims every `apy_*` name
#: whatever the module defines -- so `apy_utf8_step_of` is unreachable there
#: not because a binding is missing but because `_apy_text_of` asks Python and
#: never walks a byte.
#:
#: WHAT WOULD MAKE ONE OF THESE A BUG is the frontend learning to emit a call
#: to it. It would stop being an internal helper and become an entry point, and
#: the binding would be owed. That is why this is a ratchet in both directions:
#: a name here that HAS a binding must be taken out of it.
_HELPERS_THE_HOST_OWNS_WHOLE = {
    "apy_big_cmp_of",
    "apy_big_popcount",
    "apy_char_class_of",
    "apy_class_builtin_kind",
    "apy_class_builtin_of",
    "apy_cmp_int_double_of",
    "apy_cp_printable_of",
    "apy_either_inst_of",
    "apy_eq_raw_of",
    "apy_find_at",
    "apy_is_big_of",
    "apy_is_classlike",
    "apy_index_arg_of",
    "apy_is_descriptor_of",
    "apy_is_seq_of",
    "apy_is_set_of",
    "apy_is_special_form",
    "apy_mag_bits_of",
    "apy_mag_cmp_of",
    "apy_math_arg_of",
    "apy_names_object",
    "apy_num_f_of",
    "apy_num_order_of",
    "apy_order_of",
    "apy_order_rich_of",
    "apy_repr_entered",
    "apy_repr_left",
    "apy_rfind_at",
    "apy_str_other_of",
    "apy_type_is_sub_of",
    "apy_utf8_at_of",
    "apy_utf8_step_of",
}


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

    EVERY RETURN TYPE, not only `apy_value`. This read `APY_API apy_value`
    alone, so the eighty-odd exports answering an `int64_t` or a `double` were
    invisible to it -- and the delegate pattern the object-runtime port runs on
    produces exactly those: an exported `apy_x_of` taking and returning plain
    words, because the machine subset has one integer width and `uint32_t` is
    not it. Thirty were already unbound when the regex was widened, every one
    of them internal; `_HELPERS_THE_HOST_OWNS_WHOLE` is where they are written
    down, and why.
    """
    exported = set(re.findall(
        r"^APY_API\s+[A-Za-z_][\w \t*]*?\b(apy_\w+)\s*\(", OBJECTS_C, re.M))
    missing = sorted(exported - set(_TABLE) - UNBOUND
                     - _HELPERS_THE_HOST_OWNS_WHOLE)
    assert not missing, (
        "exported by objects/csource.py with no binding in ir/objects_host.py:\n  "
        + "\n  ".join(missing)
        + "\n\nA compiled program can call these and `asmpython run` cannot.")

    # THE OTHER DIRECTION, which is what keeps the list from becoming a place
    # names go to be forgotten: one that has since been bound, or that is no
    # longer exported at all, has to come out.
    stale = sorted(_HELPERS_THE_HOST_OWNS_WHOLE & set(_TABLE))
    assert not stale, (
        "these are bound now and must come out of "
        "_HELPERS_THE_HOST_OWNS_WHOLE:\n  " + "\n  ".join(stale))
    gone = sorted(_HELPERS_THE_HOST_OWNS_WHOLE - exported)
    assert not gone, (
        "these are not exported any more and must come out of "
        "_HELPERS_THE_HOST_OWNS_WHOLE:\n  " + "\n  ".join(gone))


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
