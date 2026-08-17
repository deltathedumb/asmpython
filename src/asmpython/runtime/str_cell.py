# The string cell, written in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the second kind to leave C. Read
# `int_cell.py` first: it is the same idiom and it explains why a constant is a
# function returning a literal (the static path has no module-level storage)
# and why this file is not importable under CPython (`i64` is not a name Python
# has, which is the honest signal that it is not host code).
#
# WHAT THIS FILE IS AND IS NOT. It is the CELL and the three constructors that
# only fill one in. It is NOT the string library: the case transforms, the
# search family and the predicates stay in C for now, and each is blocked on
# something different -- see "what stage 5 actually is" below.
#
# THE ANALYSIS THAT PRECEDED IT found that the document's plan for this stage
# was wrong in a way worth writing down. It says "a string's bytes are immortal
# too, so `str` needs nothing new", orders the work `str -> the allocator
# upgrade -> list`, and concludes the allocator is what stands between them.
# The first clause is true and the conclusion is not: nothing in `str` needs
# the allocator upgrade, and the allocator is not what is stopping `str`.
# What stops it is three other things, and only functions clear of all three
# can move now.

# ── the layout, which is C's ────────────────────────────────────────────────
#
# `struct { const char *p; int64_t n; int mut; } s;` -- the str/bytes arm of
# the union in `link/objects.py`. As with `int_cell.py` these numbers are NOT
# read off by eye: `tests/asmpython/integration/test_ported_int.py` compiles
# the real C and asserts each one with `offsetof`, and asserts the two kind
# constants with the enum's own values.
#
# THAT THE PROBE COVERS THE KINDS IS NEW, and it is the reason it was extended
# before a line of this file was written. A wrong OFFSET produces a crash or
# obvious rubbish. A wrong KIND produces a perfectly formed cell of the wrong
# type -- every field in the right place, the object simply not a str -- and
# the enum numbers one member explicitly and positions the other twenty-eight,
# with `APY_BYTES_K` inserted in the middle of it.


def apy_str_kind() -> i64:
    return 4


def apy_bytes_kind() -> i64:
    return 17


def apy_str_ptr_offset() -> i64:
    return 8


def apy_str_len_offset() -> i64:
    return 16


#: `bytearray` IS THIS FLAG AND NOTHING ELSE: same cell, same thirty shared
#: paths, writable buffer. Every constructor here leaves it zero, which
#: `apy_obj_alloc` gives for free by zeroing the payload -- the same thing
#: `int_cell.py` relies on for its cache slots. A constructor that left it
#: anything else would make a literal in read-only memory assignable.
def apy_str_mut_offset() -> i64:
    return 24


# ── construction ────────────────────────────────────────────────────────────
#
# THE BYTES ARE BORROWED, NEVER COPIED. Nothing in the cell records whether
# `p` points at a read-only global or at a heap buffer, so nothing can free it
# and nothing does -- which is exactly why these three can be written against a
# bump-pointer arena that cannot free. The constructors that OWN their bytes
# (`apy_str_take`, `apy_str_copy`) are `static` in the C, so the subset cannot
# even name them yet; promoting them is a later step and a larger one.
#
# THE TRAILING NUL IS THE CALLER'S. Every producer NUL-terminates, and the
# remaining C reads `v.s.p` as a C string in two hundred places -- `APY_CSTR`,
# `strcmp`, `snprintf`, `strtod`. The terminator is load-bearing without being
# part of the value, so the LENGTH stored here never counts it.


def apy_from_bytes(p: ptr, n: i64) -> ptr:
    """A str cell over `n` bytes at `p`, borrowed.

    The whole constructor: allocate, tag, store two fields. `mut` is left zero
    by the arena rather than written, which is the difference between a str
    and a bytearray.
    """
    cell: ptr = apy_obj_alloc(apy_str_kind())
    if not cell:
        return cell
    store(u64, u64(p), offset(cell, apy_str_ptr_offset()))
    store(i64, n, offset(cell, apy_str_len_offset()))
    return cell


def apy_bytes_literal(p: ptr, n: i64) -> ptr:
    """The same cell with the `bytes` tag.

    A SEPARATE FUNCTION RATHER THAN A FLAG, because that is what the C has and
    the two are reached from different places in the frontend. The only
    difference is the kind.
    """
    cell: ptr = apy_obj_alloc(apy_bytes_kind())
    if not cell:
        return cell
    store(u64, u64(p), offset(cell, apy_str_ptr_offset()))
    store(i64, n, offset(cell, apy_str_len_offset()))
    return cell


def apy_from_cstr(p: ptr) -> ptr:
    """A str cell over a NUL-terminated C string.

    `strlen`, WRITTEN OUT, because it is the one thing the C had here that the
    IR does not. A byte at a time is what `strlen` is; a backend that has a
    faster one is free to recognise the loop.

    THE COMPILER NO LONGER CALLS THIS FOR ITS OWN LITERALS. It used to, and
    that was a live wrong answer: the length was known at compile time, thrown
    away, and re-derived by scanning to the first NUL -- so `len("a\\0b")`
    answered 1 where Python says 3. `dynamic._dyn_str_literal` passes the
    length it already counted. This stays because a C string arriving from
    outside the program is still a real case.
    """
    n: i64 = 0
    while load(u8, offset(p, n)) != 0:
        n = n + 1
    return apy_from_bytes(p, n)
