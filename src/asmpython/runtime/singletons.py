# The singleton cells -- None, True, False, Ellipsis, NotImplemented.
#
# STAGE 5b OF docs/INERT-RUNTIME.md, and the stage that was not in the plan.
# It was found by walking into it: `list_cell.py` wrote `apy_seq_push`, which
# answers `None`, and the ported runtime may call the platform floor and its
# own `_slow` halves and NOTHING else. `apy_none()` is neither, so append was
# withdrawn and this file is what unblocks it.
#
# WHY THESE FIVE MOVE TOGETHER AND CANNOT MOVE ONE AT A TIME. They are the
# first SHARED STATE in this runtime rather than shared code. Everything
# ported so far is a function: two copies of `apy_from_int` would both build
# correct cells and nothing could tell. A singleton is the opposite -- its
# whole meaning is that there is ONE of it, because `x is None` is pointer
# equality. An IR copy beside the C's static would be a second None, every
# `is` spanning the two halves would answer False, and the failure would be a
# comparison quietly going the wrong way rather than anything that crashes.
#
# SO THE C'S OWN USES HAD TO MOVE TOO, and there were only two: `apy_error_type`
# and `apy_error_message` both said `V(&apy_none_cell)` directly rather than
# calling `apy_none()`. Both now call it, so the accessor is the only way to
# reach any of these cells and the accessor is here. That edit is in the C
# under `objects/c/`, and it is the smallest version of the thing
# `str_cell.py` warned would eventually be needed: a static promoted so the
# subset can own it.
#
# ── no initialisation step, and why that decides the design ─────────────────
#
# `reserve` gives named storage that is ZEROED, and there is nowhere to run an
# initialiser: a runtime with a startup hook is a runtime every backend has to
# remember to call, which `arena.py` says and this file obeys.
#
# So each accessor WRITES ITS OWN TAG, every call, rather than checking
# whether it has been written. Two stores at most, and they are idempotent --
# and the alternative is worse than it looks: the check would have to be
# "is the kind field still zero", and `APY_NONE_K` IS ZERO, so None could
# never distinguish "not yet built" from "built correctly". Writing
# unconditionally sidesteps the one cell where the test cannot work.


def apy_none_kind() -> i64:
    return 0


def apy_bool_kind() -> i64:
    return 1


def apy_ellipsis_kind() -> i64:
    return 20


def apy_notimpl_kind() -> i64:
    return 21


# ── the cells ───────────────────────────────────────────────────────────────
#
# ONE RESERVATION EACH, named so the storage is distinct.
#
# THE SIZE IS A LITERAL AND HAD TO BE. `reserve("...", apy_obj_size())` is
# refused with E0018 -- "reserve() needs a positive size the compiler can work
# out" -- because a reservation becomes a module-level global whose size is
# fixed before any code runs, and a call is not a constant to the pass that
# lays them out. So 152 appears here and in `int_cell.apy_obj_size`, and the
# two could drift.
#
# WHAT MAKES THAT SAFE IS THE PROBE, not care: `test_ported_int.py` asks the C
# compiler for `sizeof(apy_obj)` and asserts BOTH against it. A cell reserved
# too small would put the next singleton's tag inside this one's payload --
# and None's payload is never read, so it would stay invisible until Ellipsis
# stopped being Ellipsis.


def apy_none() -> ptr:
    """The one None. `x is None` is a pointer comparison against this."""
    cell: ptr = reserve("apy_none_cell_ir", 152)
    store(i32, i32(apy_none_kind()), offset(cell, 0))
    return cell


def apy_from_bool(b: i64) -> ptr:
    """`True` or `False`, and never a third cell.

    TWO RESERVATIONS RATHER THAN ONE PLUS AN OFFSET, so that neither depends
    on the other's size. The payload is the value itself, which is what makes
    `apy_as_int` work on a bool without a special case -- and is why CPython's
    `True + 1` is 2.
    """
    if b:
        cell: ptr = reserve("apy_true_cell_ir", 152)
        store(i32, i32(apy_bool_kind()), offset(cell, 0))
        store(i64, 1, offset(cell, 8))
        return cell
    other: ptr = reserve("apy_false_cell_ir", 152)
    store(i32, i32(apy_bool_kind()), offset(other, 0))
    store(i64, 0, offset(other, 8))
    return other


def apy_ellipsis() -> ptr:
    """`...`, whose whole behaviour is being itself.

    A SINGLETON BECAUSE `... is Ellipsis` IS THE TEST PROGRAMS WRITE, which
    the C's comment says in the same words. A fresh cell per literal would
    answer False and nothing else would look wrong.
    """
    cell: ptr = reserve("apy_ellipsis_cell_ir", 152)
    store(i32, i32(apy_ellipsis_kind()), offset(cell, 0))
    return cell


def apy_notimplemented() -> ptr:
    """`NotImplemented`: a signal, not a value.

    `x is NotImplemented` is what a reflected dunder's caller tests, so this
    has the same one-cell requirement None does, for a reason that is about
    dispatch rather than about identity being nice to have.
    """
    cell: ptr = reserve("apy_notimpl_cell_ir", 152)
    store(i32, i32(apy_notimpl_kind()), offset(cell, 0))
    return cell


# ── the sixth singleton, and the two identity operations ────────────────────
#
# `apy_stop` IS A SENTINEL, NOT A VALUE. It is what an exhausted iterator
# answers, and `apy_step` returning it is how `for` knows to stop. The C
# declares it `{ APY_NONE_K, { 0 } }` -- the same KIND as None and a
# DIFFERENT CELL -- so `apy_stop() is None` is False while both have kind
# zero. Identity here is the address and nothing else, which is exactly why it
# had to move with the other five rather than be rebuilt beside them.


def apy_stop() -> ptr:
    """The end-of-iteration sentinel.

    KIND ZERO, so zeroed storage is already a correct cell and the store
    below writes what is already there. Left in for the same reason
    `apy_none` writes its own: the tag being right by coincidence is not a
    property to rely on, and a reader should not have to know that
    `APY_NONE_K` is zero to see that this cell is built.
    """
    cell: ptr = reserve("apy_stop_cell_ir", 152)
    store(i32, i32(apy_none_kind()), offset(cell, 0))
    return cell


def apy_is_stop(v: ptr) -> i64:
    """Whether `v` is that sentinel. An ADDRESS comparison.

    Not a kind comparison, which would answer True for None as well and end
    every `for` loop on the first None an iterator produced.
    """
    if u64(v) == u64(apy_stop()):
        return 1
    return 0


def apy_is(a: ptr, b: ptr) -> ptr:
    """Python's `is`: are these the same object.

    THE WHOLE REASON THE FIVE ABOVE MOVED TOGETHER. This is pointer equality,
    so it answers True only when both sides reached the same cell -- and while
    the C owned `None` and the IR owned a copy, `x is None` would have
    compared two different addresses and answered False for a correct program.
    """
    if u64(a) == u64(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


def apy_id(v: ptr) -> ptr:
    """`id(v)`: the cell's address, as an int.

    CPython documents `id` as unique and constant for an object's lifetime,
    which an address is -- and since nothing here is ever freed, the second
    half is free too. A collector would make this the first thing to break.
    """
    return apy_from_int(i64(u64(v)))
