# `find` and `rfind`, written in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, the fourth str step and the largest group
# so far: six entry points over one shared search, and not one of them
# allocates anything but the integer it answers with. That is what makes the
# group safe to take in one step -- the whole of it is verifiable by comparing
# values, with no question about who owns a buffer.
#
# THE BOUNDS ARRIVE IN CHARACTERS AND THE SEARCH RUNS IN BYTES. That is the
# only hard part, and the C carries a comment about getting it wrong:
# answering a byte offset made `"héllo".find("ll")` say 3 where CPython says
# 2. So a bound is clamped against the CHARACTER count, converted to a byte
# offset, searched in bytes, and the answer converted back.
#
# WHAT IT DECLINES, and each is deliberate rather than unfinished:
#   * a non-str receiver or needle -- the C raises, with wording this cannot
#     reach (`apy_fail` is not callable from the subset)
#   * `index`/`rindex`, which raise ValueError when the needle is absent
#   * a bound that is not a plain int cell -- `apy_slice_arg` accepts anything
#     with `__index__`, and asking a user object for one means calling back
#     into compiled code
# Everything declined goes to the C, which already owns all of it.


def apy_str_byte_to_char(v: ptr, at: i64) -> i64:
    """How many characters occupy the first `at` bytes of `v`.

    The same walk `apy_str_char_count` does, stopped early. A continuation
    byte is `10xxxxxx` and every other byte starts a character.
    """
    p: ptr = ptr(load(u64, offset(v, apy_str_ptr_offset())))
    chars: i64 = 0
    i: i64 = 0
    while i < at:
        if (load(u8, offset(p, i)) & u8(192)) != u8(128):
            chars = chars + 1
        i = i + 1
    return chars


def apy_str_char_to_byte(v: ptr, want: i64) -> i64:
    """The byte offset of character `want`, or the byte length past the end.

    Counts character STARTS, so it lands on the first byte of the wanted
    character rather than somewhere inside the previous one.
    """
    n: i64 = load(i64, offset(v, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(v, apy_str_ptr_offset())))
    seen: i64 = 0
    i: i64 = 0
    while i < n:
        if (load(u8, offset(p, i)) & u8(192)) != u8(128):
            if seen == want:
                return i
            seen = seen + 1
        i = i + 1
    return n


def apy_str_clamp_lo(v: i64, n: i64) -> i64:
    """`apy_clamp_range`'s rule for the low bound: negative counts back from
    the end, and never past the start."""
    if v < 0:
        v = v + n
        if v < 0:
            return 0
    return v


def apy_str_clamp_hi(v: i64, n: i64) -> i64:
    """And for the high bound, which additionally never runs past the end.

    NOTE THE ASYMMETRY, which is the C's: the low bound is NOT capped at `n`.
    A `start` beyond the end leaves `lo > hi`, and the search then finds
    nothing -- which is what makes `"abc".find("", 9)` answer -1 rather than 3.
    """
    if v < 0:
        v = v + n
        if v < 0:
            return 0
    if v > n:
        return n
    return v


def apy_bytes_equal_at(a: ptr, at: i64, b: ptr, m: i64) -> bool:
    """`memcmp(a + at, b, m) == 0`, written out."""
    i: i64 = 0
    while i < m:
        if load(u8, offset(a, at + i)) != load(u8, offset(b, i)):
            return False
        i = i + 1
    return True


def apy_str_find_at(s: ptr, sub: ptr, lo: i64, hi: i64) -> i64:
    """The first occurrence of `sub` in the byte window `[lo, hi)`, or -1.

    AN EMPTY NEEDLE MATCHES AT `lo`, but only if `lo` is inside the window --
    which is the whole reason this takes `hi` rather than assuming the end.
    """
    m: i64 = load(i64, offset(sub, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    q: ptr = ptr(load(u64, offset(sub, apy_str_ptr_offset())))
    if m == 0:
        if lo <= hi:
            return lo
        return -1
    i: i64 = lo
    while i + m <= hi:
        if apy_bytes_equal_at(p, i, q, m):
            return i
        i = i + 1
    return -1


def apy_str_rfind_at(s: ptr, sub: ptr, lo: i64, hi: i64) -> i64:
    m: i64 = load(i64, offset(sub, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    q: ptr = ptr(load(u64, offset(sub, apy_str_ptr_offset())))
    if m == 0:
        if lo <= hi:
            return hi
        return -1
    i: i64 = hi - m
    while i >= lo:
        if apy_bytes_equal_at(p, i, q, m):
            return i
        i = i - 1
    return -1


def apy_str_searchable(s: ptr, sub: ptr) -> bool:
    """Both operands are plain strings, which is all this file handles."""
    return apy_is_str(s) and apy_is_str(sub)


def apy_str_seek(s: ptr, sub: ptr, lo: i64, hi: i64, backwards: bool) -> ptr:
    """The shared body: clamp in characters, search in bytes, answer in
    characters."""
    chars: i64 = apy_str_char_count(s)
    lo = apy_str_clamp_lo(lo, chars)
    hi = apy_str_clamp_hi(hi, chars)
    # A START PAST THE END FINDS NOTHING, INCLUDING NOTHING. CPython:
    # `"abc".find("", 3)` is 3 and `"abc".find("", 4)` is -1, so the boundary
    # is `start > len` and not `start >= len`.
    #
    # THE C GETS THIS WRONG AND THIS DELIBERATELY DOES NOT. `apy_clamp_range`
    # leaves `lo` uncapped -- correctly, because `apy_find_at` compares it
    # against `hi` and answers -1 -- but the character-to-byte conversion in
    # between then clamps 99 down to the byte length, and the comparison that
    # was supposed to catch it becomes `11 <= 11`. So the compiled backend
    # answered `"hello world".find("", 99)` as 11 where CPython and the host
    # runtime both say -1. Verified against a build with this port removed:
    # the C already did it.
    #
    # Reproducing that faithfully was the alternative, and it is the wrong
    # one. CPython is the oracle for this project and the host runtime already
    # agrees with it, so keeping the C's answer would have preserved a
    # two-runtime divergence for the sake of a diff against the half that is
    # wrong.
    if lo > chars:
        return apy_from_int(-1)
    blo: i64 = apy_str_char_to_byte(s, lo)
    bhi: i64 = apy_str_char_to_byte(s, hi)
    if backwards:
        at: i64 = apy_str_rfind_at(s, sub, blo, bhi)
    else:
        at = apy_str_find_at(s, sub, blo, bhi)
    if at < 0:
        return apy_from_int(at)
    return apy_from_int(apy_str_byte_to_char(s, at))


def apy_str_bound(v: ptr) -> i64:
    """A bound as a machine integer. Only an exact int cell; see the header."""
    return apy_int_payload(v)


def apy_str_find(s: ptr, sub: ptr) -> ptr:
    if apy_str_searchable(s, sub):
        return apy_str_seek(s, sub, 0, apy_str_char_count(s), False)
    return apy_str_find_slow(s, sub)


def apy_str_rfind(s: ptr, sub: ptr) -> ptr:
    if apy_str_searchable(s, sub):
        return apy_str_seek(s, sub, 0, apy_str_char_count(s), True)
    return apy_str_rfind_slow(s, sub)


def apy_str_find2(s: ptr, sub: ptr, start: ptr) -> ptr:
    if apy_str_searchable(s, sub) and apy_is_int(start):
        return apy_str_seek(s, sub, apy_str_bound(start),
                            apy_str_char_count(s), False)
    return apy_str_find2_slow(s, sub, start)


def apy_str_rfind2(s: ptr, sub: ptr, start: ptr) -> ptr:
    if apy_str_searchable(s, sub) and apy_is_int(start):
        return apy_str_seek(s, sub, apy_str_bound(start),
                            apy_str_char_count(s), True)
    return apy_str_rfind2_slow(s, sub, start)


def apy_str_find3(s: ptr, sub: ptr, start: ptr, end: ptr) -> ptr:
    if apy_str_searchable(s, sub) and apy_is_int(start) and apy_is_int(end):
        return apy_str_seek(s, sub, apy_str_bound(start),
                            apy_str_bound(end), False)
    return apy_str_find3_slow(s, sub, start, end)


def apy_str_rfind3(s: ptr, sub: ptr, start: ptr, end: ptr) -> ptr:
    if apy_str_searchable(s, sub) and apy_is_int(start) and apy_is_int(end):
        return apy_str_seek(s, sub, apy_str_bound(start),
                            apy_str_bound(end), True)
    return apy_str_rfind3_slow(s, sub, start, end)


# -- the three the search and the split both stand on ----------------------


def apy_find_at(s: ptr, sub: ptr, lo: i64, hi: i64) -> i64:
    """The first `sub` inside `s[lo:hi]`, as an absolute byte index, or -1.

    AN EMPTY NEEDLE MATCHES AT `lo` -- but only when `lo` is inside the
    window, which is the whole reason this takes `hi` rather than assuming
    the end of the string.

    BYTES, AND THAT IS RIGHT: the callers hand it byte bounds and read a byte
    answer, and the conversion to character positions happens above them.
    Doing it here would convert twice.

    THE INNER LOOP ENDS BY MOVING `j` TO `m`, because the subset has no
    `break` -- which reads oddly and is the only way to leave a loop early
    without a second flag.
    """
    m: i64 = load(i64, offset(sub, apy_str_len_offset()))
    if m == 0:
        if lo <= hi:
            return lo
        return -1
    sp: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    np: ptr = ptr(load(u64, offset(sub, apy_str_ptr_offset())))
    i: i64 = lo
    while i + m <= hi:
        j: i64 = 0
        same: i64 = 1
        while j < m:
            if load(u8, offset(sp, i + j)) != load(u8, offset(np, j)):
                same = 0
                j = m
            else:
                j = j + 1
        if same:
            return i
        i = i + 1
    return -1


def apy_rfind_at(s: ptr, sub: ptr, lo: i64, hi: i64) -> i64:
    """The LAST `sub` inside `s[lo:hi]`, or -1.

    AN EMPTY NEEDLE MATCHES AT `hi` here, which is the mirror of the rule
    above and is what makes `"abc".rfind("")` answer 3.
    """
    m: i64 = load(i64, offset(sub, apy_str_len_offset()))
    if m == 0:
        if lo <= hi:
            return hi
        return -1
    sp: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    np: ptr = ptr(load(u64, offset(sub, apy_str_ptr_offset())))
    i: i64 = hi - m
    while i >= lo:
        j: i64 = 0
        same: i64 = 1
        while j < m:
            if load(u8, offset(sp, i + j)) != load(u8, offset(np, j)):
                same = 0
                j = m
            else:
                j = j + 1
        if same:
            return i
        i = i - 1
    return -1


def apy_str_slice_of(s: ptr, lo: i64, hi: i64) -> ptr:
    """`s[lo:hi]` by BYTE bounds, as a string of its own.

    A REVERSED WINDOW IS EMPTY rather than an error, which is what every
    caller wants: a search that found nothing hands in bounds that crossed,
    and the answer is the empty string.

    COPIED, NOT POINTED AT: the piece outlives the call, and a slice sharing
    the original's bytes could not carry its own terminator.
    """
    if hi < lo:
        hi = lo
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    return apy_str_copy_bytes(offset(p, lo), hi - lo)


def apy_arg_must_be_str_of(meth: ptr, argno: i64, v: ptr) -> ptr:
    """`s.count(5)` -- the argument had to be a string and was not.

    `None` IS NAMED AS `None` AND NOT AS `NoneType`, which is what CPython
    prints here: the message is about the VALUE passed, and None is the value
    a program wrote.

    THE POSITION IS PART OF THE MESSAGE only when there is more than one
    argument to confuse -- a one-argument method names no number, which is
    why there are two shapes rather than one with a zero in it.
    """
    k: ptr = apy_kind_name_of(v)
    if i64(load(i32, offset(v, 0))) == apy_none_kind():
        k = rodata(b"None\0")
    if argno:
        buf: ptr = apy_fmt_scratch()
        at: i64 = apy_cstr_into(buf, 0, 200, meth)
        at = apy_cstr_into(buf, at, 200, rodata(b"() argument \0"))
        at = apy_cstr_into(buf, at, 200, apy_decimal_of(argno, 0))
        at = apy_cstr_into(buf, at, 200, rodata(b" must be str, not \0"))
        at = apy_cstr_into(buf, at, 200, k)
        store(u8, u8(0), offset(buf, at))
        return apy_raise_at(rodata(b"TypeError\0"), buf)
    return apy_raise_fmt(
        rodata(b"TypeError\0"),
        rodata(b"%s() argument must be str, not %s\0"), meth, k)


def apy_str_other_of(meth: ptr, argno: i64, v: ptr) -> i64:
    """Is `v` a string this method may work on?

    BYTES TOO, for the reason every string operation here admits them: the
    two share a layout, and the operation is the same one.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_str_kind() or k == apy_bytes_kind():
        return 1
    apy_arg_must_be_str_of(meth, argno, v)
    return 0


def apy_str_count_in_of(s: ptr, sub: ptr, start: ptr, end: ptr) -> ptr:
    """`s.count(sub)`, with optional bounds. NON-OVERLAPPING.

    `"aaa".count("aa")` IS 1 AND NOT 2, which is what the `i += m` says: a
    match consumes what it matched, so the second `aa` starting one byte in
    is never looked for.

    AN EMPTY NEEDLE MATCHES BETWEEN EVERY PAIR and at both ends, which is
    `hi - lo + 1` positions -- and zero when the window is empty.

    BYTES, AND THAT IS RIGHT HERE: a non-empty needle can only match at a
    character boundary, because UTF-8 is prefix-free at those boundaries. The
    empty needle counts POSITIONS, which is a byte answer for a byte window,
    and the C answers the same.
    """
    if not apy_str_other_of(rodata(b"count\0"), 1, sub):
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
    m: i64 = load(i64, offset(sub, apy_str_len_offset()))
    if m == 0:
        if hi >= lo:
            return apy_from_int(hi - lo + 1)
        return apy_from_int(0)
    sp: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    np: ptr = ptr(load(u64, offset(sub, apy_str_ptr_offset())))
    hits: i64 = 0
    i: i64 = lo
    while i + m <= hi:
        j: i64 = 0
        same: i64 = 1
        while j < m:
            if load(u8, offset(sp, i + j)) != load(u8, offset(np, j)):
                same = 0
                j = m
            else:
                j = j + 1
        if same:
            hits = hits + 1
            i = i + m
        else:
            i = i + 1
    return apy_from_int(hits)


def apy_str_count2(s: ptr, sub: ptr, start: ptr) -> ptr:
    """`s.count(sub, start)`."""
    if not apy_str_self_of(rodata(b"count\0"), s):
        return ptr(0)
    return apy_str_count_in_of(s, sub, start, ptr(0))


def apy_str_count3(s: ptr, sub: ptr, start: ptr, end: ptr) -> ptr:
    """`s.count(sub, start, end)`."""
    if not apy_str_self_of(rodata(b"count\0"), s):
        return ptr(0)
    return apy_str_count_in_of(s, sub, start, end)
