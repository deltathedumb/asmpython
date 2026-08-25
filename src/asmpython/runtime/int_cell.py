# The integer cell, written in the machine subset.
#
# STAGE 3 OF docs/INERT-RUNTIME.md, and the first piece of asmpython's object
# runtime that is not C. It is compiled by asmpython's own Python frontend on
# the STATIC path -- so it allocates nothing implicitly, calls no `apy_*` it
# does not name, and lowers to IR every backend already understands.
#
# WHAT MAKES THIS THE RIGHT FIRST KIND. An int is the smallest complete object:
# a tag and a payload, one cache, no ownership and no variable-length part. It
# is also the most-called constructor in the runtime -- every integer literal,
# every loop counter and every length goes through it -- so if the ported
# version is wrong, nothing works rather than something obscure fails.
#
# THIS FILE IS NOT IMPORTED BY ANYTHING. It is read as SOURCE and compiled into
# every program that needs the object runtime; see `objects/ir.py`. Under
# CPython it is not even valid -- `i64` is not a name Python has -- and that is
# the honest signal that it is not host code.

# ── the layout, which is C's ────────────────────────────────────────────────
#
# `struct apy_obj { int kind; union { int64_t i; double f; ... } v; }`, as
# `objects/csource.py` declares it. These numbers are NOT guessed: a test compiles
# that C and asserts `sizeof` and each `offsetof` against them, because a cell
# this file builds is read by 15,000 lines of C that were not told anything had
# changed. See `tests/asmpython/integration/test_ported_int.py`.
#
# Written as functions rather than as module constants because the static path
# has no module-level storage, and a function returning a literal costs nothing
# -- it is inlined by every backend that inlines anything and is three
# instructions where it is not.


def apy_obj_size() -> i64:
    return 152


def apy_kind_offset() -> i64:
    return 0


def apy_payload_offset() -> i64:
    return 8


#: `APY_INT_K`, the third entry of the kind enum in `objects/csource.py`.
def apy_int_kind() -> i64:
    return 2


# ── the small-integer cache ─────────────────────────────────────────────────
#
# CPython shares the cells for -5..256, and it is OBSERVABLE: `a = 1; b = 1;
# a is b` is True, and `a = 257; b = 257; a is b` is False. The range is
# CPython's own because the answer has to agree at the boundary too -- 256 is
# shared and 257 is not, and a program can see which.
#
# 262 slots of eight bytes. `reserve` gives static storage that is ZEROED, so
# an empty slot is a null pointer and "have I filled this one" needs no
# separate flag -- which is the same trick the C plays with a NULL slot.

def apy_small_lo() -> i64:
    return -5


def apy_small_hi() -> i64:
    return 256


def apy_small_slot(i: i64) -> ptr:
    return offset(reserve("apy_small_ir", 2096), (i - apy_small_lo()) * 8)


# ── construction ────────────────────────────────────────────────────────────

def apy_int_alloc(value: i64) -> ptr:
    """One int cell, tagged and filled. Never shared.

    Through `apy_obj_alloc` (`arena.py`) rather than `plat_heap` directly, so
    that every object in a program -- the C runtime's and this file's alike --
    comes from one allocator. It was `plat_heap` when this was the only ported
    kind, which was a malloc per integer and hit the platform floor on every
    allocation.
    """
    cell: ptr = apy_obj_alloc(apy_int_kind())
    if not cell:
        return cell
    store(i64, value, offset(cell, apy_payload_offset()))
    return cell


def apy_from_int(i: i64) -> ptr:
    """An `int` object for `i`. The runtime's most-called constructor.

    Small values come from the cache, filled lazily: there is no init hook to
    hang a loop on, and a null slot is a cheaper test than a "have I run yet"
    flag.
    """
    if i >= apy_small_lo() and i <= apy_small_hi():
        slot: ptr = apy_small_slot(i)
        shared: ptr = ptr(load(u64, slot))
        if not shared:
            shared = apy_int_alloc(i)
            store(u64, u64(shared), slot)
        return shared
    return apy_int_alloc(i)


def apy_as_int(v: ptr) -> i64:
    """The payload of an int cell.

    NO KIND CHECK, exactly as the C has none: every caller has already
    dispatched on the kind, and a check here would be a second opinion that
    can disagree with the first. A path that answers for kinds it was never
    told about is worse than one that refuses -- see the traps in
    docs/INERT-RUNTIME.md -- and the answer to that is to keep the dispatch in
    one place, not to add a test everywhere.
    """
    return load(i64, offset(v, apy_payload_offset()))
