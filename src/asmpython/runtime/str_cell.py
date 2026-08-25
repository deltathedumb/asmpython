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
# the union in `objects/csource.py`. As with `int_cell.py` these numbers are NOT
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


def apy_str_copy_bytes(p: ptr, n: i64) -> ptr:
    """`n` bytes COPIED into storage the cell owns, plus a terminator.

    THE ONE THE RUNTIME BUILDS EVERY STRING WITH. Twenty-four places in the C
    reach it through the `apy_str_copy` shim -- every slice, join, case
    transform, repr and format -- so this is the function that decides where a
    compiled program's strings live. It was `malloc`; it is the arena now,
    which is what "the object runtime allocates from one place" finally means
    for a string's BYTES and not just for its cell.

    A BUMP POINTER CANNOT FREE, AND THAT COSTS NOTHING HERE -- checked rather
    than assumed. Nothing in the C frees `v.s.p`: all 51 `free()` calls
    release transient locals, never the buffer handed to `apy_str_take`. A
    string's bytes already lived until the program exited, so this changes the
    allocator and not the lifetime.

    THE TERMINATOR IS WRITTEN AND IS NOT PART OF THE LENGTH. Two hundred
    places in the remaining C read `v.s.p` as a C string, so a cell built
    without one is a cell the rest of the runtime reads off the end of. The
    arena is asked for `n + 1` for exactly that byte.
    """
    buf: ptr = apy_alloc_bytes(n + 1)
    if not buf:
        return buf
    i: i64 = 0
    while i < n:
        store(u8, load(u8, offset(p, i)), offset(buf, i))
        i = i + 1
    store(u8, u8(0), offset(buf, n))
    return apy_from_bytes(buf, n)


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


# ── the two wrappers that CANNOT move, and why ─────────────────────────────
#
# `apy_str_copy` and `apy_bytes_copy` were written, declared, and taken back
# out. They look like the easiest ports left -- each is one call to
# `apy_str_copy_bytes`, which is already IR -- and the C explains in its own
# words why they are not:
#
#     The parameter is an `apy_value` and not a `const char *` because that is
#     what an IR `ptr` compiles to: a ported definition emits
#     `uintptr_t apy_str_copy_bytes(uintptr_t, int64_t)`, and a C prototype
#     spelling the first argument as a pointer is a CONFLICTING TYPE where gcc
#     sees both.
#
# THE SPLIT IS ALREADY THE ANSWER. `apy_str_copy_bytes` exists because the
# half that ALLOCATES could take an `apy_value` and move; `apy_str_copy` keeps
# `const char *` so that the twenty-four call sites in the C -- every slice,
# join, case transform and repr -- did not each need a cast. Porting the
# wrapper would undo the arrangement that made porting the body possible.
#
# HOW IT SURFACED, which is the part worth keeping: not as a wrong answer but
# as `apy_str_copy used but never defined` at link time, because the C omitted
# the body it was told was ported while gcc refused the mismatched prototype.
# A signature that disagrees across the boundary fails loudly, which is the
# one thing this whole arrangement gets for free.


# -- bytes.hex() and bytes.fromhex(), which the arena made sayable ----------
#
# NEITHER NEEDED LIBC. The C reaches `malloc` for a working buffer and
# `fputs`/`exit` for the out-of-memory abort beside it; the arena answers null
# and the caller propagates, which is the convention every ported function
# here already follows -- so the abort path simply does not exist.


def apy_hex_digit(d: i64) -> i64:
    """One lowercase hex digit, as a byte."""
    if d < 10:
        return 48 + d
    return 87 + d


def apy_bytes_hex(b: ptr, sep: ptr) -> ptr:
    """`b.hex()` and `b.hex(sep)`.

    A ONE-CHARACTER SEPARATOR AND NOTHING ELSE, which is what the C accepts:
    anything longer is ignored rather than refused, and the default is no
    separator at all.

    THE BUFFER IS SIZED FOR THE WORST CASE -- three bytes per input byte,
    which is two digits and a separator -- and the terminator makes it
    `n * 3 + 2`. Over-allocating a few bytes in a bump arena costs a few
    bytes.
    """
    if i64(load(i32, offset(b, 0))) != apy_bytes_kind():
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute 'hex'%s\0"),
            apy_kind_name_of(b), rodata(b"\0"))
    s: i64 = 0
    if i64(load(i32, offset(sep, 0))) == apy_str_kind():
        if load(i64, offset(sep, apy_str_len_offset())) == 1:
            s = i64(load(u8, ptr(load(u64, offset(
                sep, apy_str_ptr_offset())))))
    n: i64 = load(i64, offset(b, apy_str_len_offset()))
    buf: ptr = apy_alloc_bytes(n * 3 + 2)
    if not buf:
        return buf
    src: ptr = ptr(load(u64, offset(b, apy_str_ptr_offset())))
    out: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = i64(load(u8, offset(src, i)))
        if s:
            if i:
                store(u8, u8(s), offset(buf, out))
                out = out + 1
        store(u8, u8(apy_hex_digit(c >> 4)), offset(buf, out))
        store(u8, u8(apy_hex_digit(c & 15)), offset(buf, out + 1))
        out = out + 2
        i = i + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)


def apy_hex_value(c: i64) -> i64:
    """A hex digit's value, or -1 if it is not one."""
    if c >= 48 and c <= 57:
        return c - 48
    if c >= 97 and c <= 102:
        return c - 97 + 10
    if c >= 65 and c <= 70:
        return c - 65 + 10
    return -1


def apy_bytes_fromhex(self: ptr, text: ptr) -> ptr:
    """`bytes.fromhex(text)`.

    WHITESPACE BETWEEN PAIRS IS SKIPPED, which is Python\'s rule and is what
    lets a hex dump be pasted in. Whitespace INSIDE a pair is not special --
    it is skipped too, so `"a b"` is a single odd digit and refused.

    AN ODD NUMBER OF DIGITS IS AN ERROR, caught after the walk: a trailing
    high nibble with nothing to pair it with is exactly that.
    """
    if i64(load(i32, offset(text, 0))) != apy_str_kind():
        return apy_raise_at(
            rodata(b"TypeError\0"),
            rodata(b"fromhex() argument must be str\0"))
    n: i64 = load(i64, offset(text, apy_str_len_offset()))
    buf: ptr = apy_alloc_bytes(n // 2 + 2)
    if not buf:
        return buf
    src: ptr = ptr(load(u64, offset(text, apy_str_ptr_offset())))
    out: i64 = 0
    hi: i64 = -1
    i: i64 = 0
    while i < n:
        c: i64 = i64(load(u8, offset(src, i)))
        if c == 32 or c == 9 or c == 10:
            i = i + 1
        else:
            d: i64 = apy_hex_value(c)
            if d < 0:
                return apy_raise_at(
                    rodata(b"ValueError\0"),
                    rodata(b"non-hexadecimal number found in "
                           b"fromhex() arg\0"))
            if hi < 0:
                hi = d
            else:
                store(u8, u8((hi << 4) | d), offset(buf, out))
                out = out + 1
                hi = -1
            i = i + 1
    if hi >= 0:
        return apy_raise_at(
            rodata(b"ValueError\0"),
            rodata(b"non-hexadecimal number found in fromhex() arg\0"))
    store(u8, u8(0), offset(buf, out))
    return apy_bytes_literal(buf, out)
