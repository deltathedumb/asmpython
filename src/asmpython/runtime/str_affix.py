# `startswith` and `endswith`, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md. The C reaches both through one static
# worker, `apy_affix`, which also handles a start and an end index, a TUPLE of
# prefixes to try, and the AttributeError for a receiver that is not a string.
# None of that is nameable from the subset, so these are splits -- and the
# case they take is the one a program writes: one string, one prefix, no
# bounds.
#
# ONLY THE TWO-ARGUMENT FORMS. `apy_str_startswith2` and `...3` take a start
# and an end, and clamping those is `apy_slice_arg` and `apy_clamp_range`,
# both static. They are separate exported functions, so declining them costs
# nothing here: they simply are not split, and every call still reaches the C.
#
# A TUPLE OF PREFIXES IS DECLINED. `s.startswith(("a", "b"))` is real Python
# and the C loops over it; the kind check below sends it back, which is the
# same decline every other split in this runtime makes for a shape it was not
# written for.
#
# ── bytes, not characters, and that is right here ──────────────────────────
#
# The C compares with `memcmp` and so does this. It is worth saying why that
# is not the byte-versus-character bug the rest of the string work exists to
# fix: a prefix relation over UTF-8 BYTES is the same relation as over
# characters, because a valid encoding is prefix-free at character
# boundaries. `s.startswith(t)` cannot be true byte-wise and false
# character-wise unless `t` is not a whole string, which it is.
#
# That is NOT true of `len`, of indexing, or of `find`'s ANSWER -- all of
# which count, and all of which had to be taught characters. This one only
# ever answers a bool, so it never has a position to get wrong.


def apy_str_startswith(s: ptr, fix: ptr) -> ptr:
    """`s.startswith(fix)` for two plain strings."""
    if i64(load(i32, offset(s, 0))) != apy_str_kind():
        return apy_str_startswith_slow(s, fix)
    if i64(load(i32, offset(fix, 0))) != apy_str_kind():
        return apy_str_startswith_slow(s, fix)
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    m: i64 = load(i64, offset(fix, apy_str_len_offset()))
    if m > n:
        return apy_from_bool(0)
    sp: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    fp: ptr = ptr(load(u64, offset(fix, apy_str_ptr_offset())))
    at: i64 = 0
    while at < m:
        if load(u8, offset(sp, at)) != load(u8, offset(fp, at)):
            return apy_from_bool(0)
        at = at + 1
    return apy_from_bool(1)


def apy_str_endswith(s: ptr, fix: ptr) -> ptr:
    """`s.endswith(fix)` for two plain strings.

    THE OFFSET IS THE ONLY DIFFERENCE from the function above: the comparison
    starts `n - m` bytes in. Written out rather than shared, because the
    shared version would take a flag and branch on it inside the loop.

    AN EMPTY SUFFIX IS TRUE, which falls out of `m` being zero rather than
    being special-cased -- the loop runs no times and the answer is the one
    Python gives for `"abc".endswith("")`.
    """
    if i64(load(i32, offset(s, 0))) != apy_str_kind():
        return apy_str_endswith_slow(s, fix)
    if i64(load(i32, offset(fix, 0))) != apy_str_kind():
        return apy_str_endswith_slow(s, fix)
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    m: i64 = load(i64, offset(fix, apy_str_len_offset()))
    if m > n:
        return apy_from_bool(0)
    sp: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    fp: ptr = ptr(load(u64, offset(fix, apy_str_ptr_offset())))
    base: i64 = n - m
    at: i64 = 0
    while at < m:
        if load(u8, offset(sp, base + at)) != load(u8, offset(fp, at)):
            return apy_from_bool(0)
        at = at + 1
    return apy_from_bool(1)


# ── the whole of `startswith` and `endswith`, bounds and tuples included ────
#
# WHAT THE SPLITS ABOVE DECLINED. The two fast paths keep their place -- they
# are a byte compare and nothing else, which is what a program writes -- and
# this is the worker underneath them, the one that also answers a tuple of
# prefixes, a start and an end.


def apy_affix_bounds() -> ptr:
    """Two words: the low and high bound being clamped.

    RESERVED RATHER THAN STACK-ALLOCATED, because `apy_slice_arg_of` and
    `apy_clamp_range_of` both write through a pointer and the subset's
    `alloca` has no other user here. Safe because nothing this reaches can
    call back into it: the bounds are read into locals before the first
    thing that could.
    """
    return reserve("apy_affix_bounds_ir", 16)


def apy_affix_of(s: ptr, fix: ptr, start: ptr, end: ptr,
                 at_end: i64) -> ptr:
    """`s.startswith(fix)` and `s.endswith(fix)`, in full.

    ONE WORKER FOR BOTH, because the two differ only in where the compare
    begins -- and `at_end` is carried all the way down to `apy_affix1_of`
    rather than being branched on here.

    A TUPLE IS ANY-OF, and an empty one is False: `s.startswith(())` is
    False in Python, which falls out of the loop rather than needing a case.
    A non-str INSIDE the tuple is refused naming the method, which is what
    CPython does and why the message is built here rather than by the
    element check.

    BYTES TOO -- `b"ab".startswith(b"a")` is the same operation on the same
    layout, which is why the kind test admits both.

    THE BOUNDS ARE A SLICE AND NOT AN INDEX, so `"abc".startswith("c", 99)`
    is False rather than an IndexError: `apy_slice_arg_of` clamps where an
    index would refuse.
    """
    meth: ptr = rodata(b"startswith\0")
    if at_end:
        meth = rodata(b"endswith\0")
    if not apy_str_self_of(meth, s):
        return ptr(0)
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    bounds: ptr = apy_affix_bounds()
    store(i64, 0, bounds)
    store(i64, n, offset(bounds, 8))
    if start:
        if not apy_slice_arg_of(start, bounds):
            return ptr(0)
    if end:
        if not apy_slice_arg_of(end, offset(bounds, 8)):
            return ptr(0)
    apy_clamp_range_of(n, bounds, offset(bounds, 8))
    lo: i64 = load(i64, bounds)
    hi: i64 = load(i64, offset(bounds, 8))
    if i64(load(i32, offset(fix, 0))) == apy_tuple_kind():
        count: i64 = load(i64, offset(fix, apy_q_n_offset()))
        items: ptr = ptr(load(u64, offset(fix, apy_q_items_offset())))
        i: i64 = 0
        while i < count:
            one: ptr = ptr(load(u64, offset(items, i * apy_value_size())))
            if i64(load(i32, offset(one, 0))) != apy_str_kind():
                return apy_raise_fmt(
                    rodata(b"TypeError\0"),
                    rodata(b"tuple for %s must only contain str, "
                           b"not %s\0"),
                    meth, apy_kind_name_of(one))
            if apy_affix1_of(s, one, lo, hi, at_end):
                return apy_from_bool(1)
            i = i + 1
        return apy_from_bool(0)
    k: i64 = i64(load(i32, offset(fix, 0)))
    if k != apy_str_kind() and k != apy_bytes_kind():
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"%s first arg must be str or a tuple of str, "
                   b"not %s\0"),
            meth, apy_kind_name_of(fix))
    return apy_from_bool(apy_affix1_of(s, fix, lo, hi, at_end))


def apy_str_startswith2(s: ptr, fix: ptr, start: ptr) -> ptr:
    """`s.startswith(fix, start)`."""
    return apy_affix_of(s, fix, start, ptr(0), 0)


def apy_str_startswith3(s: ptr, fix: ptr, start: ptr, end: ptr) -> ptr:
    """`s.startswith(fix, start, end)`."""
    return apy_affix_of(s, fix, start, end, 0)


def apy_str_endswith2(s: ptr, fix: ptr, start: ptr) -> ptr:
    """`s.endswith(fix, start)`."""
    return apy_affix_of(s, fix, start, ptr(0), 1)


def apy_str_endswith3(s: ptr, fix: ptr, start: ptr, end: ptr) -> ptr:
    """`s.endswith(fix, start, end)`."""
    return apy_affix_of(s, fix, start, end, 1)
