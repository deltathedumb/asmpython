# Asking what kind a value is, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md. Every function here reads the tag at
# offset 0 and answers a number or a bool -- no allocation, no buffer, no
# ownership question, and nothing that can fail. That is what makes them
# portable ahead of the kinds they ask about: `apy_match_map` can say whether
# a value is a dict long before `dict` itself leaves the C.
#
# THE KIND CONSTANTS ARE THE WHOLE RISK, and it is not the obvious one. A
# wrong OFFSET crashes or returns rubbish; a wrong KIND returns a perfectly
# ordinary `False` from a predicate that should have said `True`, and the
# program then takes the other branch and does something reasonable-looking.
# The enum numbers ONE member explicitly and positions the other twenty-eight,
# so the C compiler is the only thing that knows these values --
# `test_ported_int.py` asks it and compares, and nothing here is read off by
# eye.


def apy_dict_kind() -> i64:
    return 7


def apy_inst_kind() -> i64:
    return 14


def apy_is_instance(v: ptr) -> i64:
    """Whether `v` is an instance of a user-defined class.

    NOT `isinstance`. This asks about the CELL -- is the thing an instance
    object at all -- where `apy_isinstance` asks whether a value belongs to a
    given class. The two names are one letter apart and answer different
    questions, which is worth knowing before reading either.
    """
    if i64(load(i32, offset(v, 0))) == apy_inst_kind():
        return 1
    return 0


def apy_match_seq(v: ptr) -> i64:
    """Whether a `case [a, b]` pattern may match this.

    LIST OR TUPLE, and nothing else. A str is a sequence in Python and
    deliberately does NOT match a sequence pattern -- `case [x]` against
    `"a"` is False, because matching a string element-wise is almost never
    what someone meant. CPython draws the line in the same place.
    """
    kind: i64 = i64(load(i32, offset(v, 0)))
    if kind == apy_list_kind():
        return 1
    if kind == apy_tuple_kind():
        return 1
    return 0


def apy_match_map(v: ptr) -> i64:
    """Whether a `case {"k": v}` pattern may match this."""
    if i64(load(i32, offset(v, 0))) == apy_dict_kind():
        return 1
    return 0


def apy_as_bool(v: ptr) -> i64:
    """The payload of a bool cell, as 0 or 1.

    READS THE PAYLOAD, NOT THE TRUTH. This is for a value already known to be
    a bool -- `apy_truth` is the one that asks whether an arbitrary value is
    truthy, and it has to look at the kind first. Handed a list, this reads
    the items pointer and answers "true" for every list including the empty
    one, which is why the caller owes the check.
    """
    if load(i64, offset(v, apy_payload_offset())) != 0:
        return 1
    return 0


def apy_to_bool(v: ptr) -> ptr:
    """`bool(v)` -- the truthiness of any value, as a bool cell."""
    return apy_from_bool(apy_truth(v))
