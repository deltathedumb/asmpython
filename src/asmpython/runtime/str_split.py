# `split` and `rsplit` on a separator, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the first ported function that builds
# a LIST. Every string family before this one answered with a bool or with one
# string; this one needs `apy_list_new` and `apy_seq_push`, both of which are
# ported, which is the whole reason the sequence work came first.
#
# NO ASCII GATE, and for the same reason `removeprefix` needs none: the
# separator is a WHOLE STRING, so finding it byte-wise finds it exactly where
# it is character-wise -- a valid UTF-8 encoding cannot contain the bytes of
# one character starting part-way through another. Every piece the split hands
# back is cut at a separator boundary, so every piece is a whole string.
#
# That is what separates this file from `runtime/str_strip.py`, which needs
# the gate: `strip` asks a question about each character, and this asks one
# about a substring.
#
# ── rsplit IS split, without a limit ───────────────────────────────────────
#
# `'a,b,c'.rsplit(',')` and `'a,b,c'.split(',')` are the same list. Direction
# only matters once there is a `maxsplit` to run out of, and the two-argument
# forms have none -- so `apy_str_rsplit` below is not an approximation of the
# C's backwards walk, it is the same answer reached forwards.
#
# THE C DOES WALK BACKWARDS and then reverses the list it built, because it
# has a `maxsplit` to honour and one body serving all six wrappers. Written
# here, that walk would be code whose only observable effect is to undo
# itself.
#
# ── what is declined ───────────────────────────────────────────────────────
#
# A `maxsplit` -- `apy_str_split_n` and `apy_str_rsplit_n` are separate
# exported functions and simply are not split, so every call with a limit
# still reaches the C.
#
# `sep` OF None, which means "split on runs of whitespace" -- a different
# operation with its own worker, and one that WOULD need the ASCII gate.
# `apy_is_str` declines it along with a bytes separator and the TypeError.
#
# AN EMPTY SEPARATOR, which is a ValueError the C words itself.


def apy_str_split_all(s: ptr, sep: ptr, m: i64) -> ptr:
    """Every piece of `s` between occurrences of `sep`.

    THE TRAILING PUSH IS NOT AN EDGE CASE, it is the definition: a split on
    `k` separators has `k + 1` pieces, so the loop handles the ones that end
    at a separator and the line after it handles the one that ends at the
    string. `'a,'.split(',')` is `['a', '']` and `','.split(',')` is
    `['', '']`, both of which fall out of that rather than being tested for.

    THE CAPACITY IS A GUESS AND ONLY A GUESS. Eight is what the C asks for;
    `apy_seq_push` grows the block when it runs out, so the number costs a
    reallocation at worst and never an answer.
    """
    out: ptr = apy_list_new(8)
    if not out:
        return out
    n: i64 = apy_str_byte_len(s)
    i: i64 = 0
    at: i64 = apy_str_find_at(s, sep, i, n)
    while at >= 0:
        apy_seq_push(out, apy_str_slice_new(s, i, at))
        i = at + m
        at = apy_str_find_at(s, sep, i, n)
    apy_seq_push(out, apy_str_slice_new(s, i, n))
    return out


def apy_str_split_ok(s: ptr, sep: ptr) -> i64:
    """The separator's byte length if this file may do the split, else -1."""
    if not apy_is_str(s):
        return -1
    if not apy_is_str(sep):
        return -1
    m: i64 = apy_str_byte_len(sep)
    if m == 0:
        return -1
    return m


def apy_str_split(s: ptr, sep: ptr) -> ptr:
    """`s.split(sep)` for two plain strings."""
    m: i64 = apy_str_split_ok(s, sep)
    if m < 0:
        return apy_str_split_slow(s, sep)
    return apy_str_split_all(s, sep, m)


def apy_str_rsplit(s: ptr, sep: ptr) -> ptr:
    """`s.rsplit(sep)` -- the same list, reached forwards. See the header."""
    m: i64 = apy_str_split_ok(s, sep)
    if m < 0:
        return apy_str_rsplit_slow(s, sep)
    return apy_str_split_all(s, sep, m)


# -- split and rsplit, both modes ------------------------------------------
#
# THE TWO SPLIT MODES ARE DIFFERENT ALGORITHMS, not one with a default
# separator, and the case that shows it is `"  a  b  "`: with no argument it
# splits on RUNS of whitespace and drops the empty pieces at both ends, giving
# ["a", "b"]; with `" "` it splits on each single space and keeps them, giving
# ["", "", "a", "", "b", "", ""]. A default of `" "` would answer the second
# to both.


def apy_seq_reverse_of(out: ptr) -> ptr:
    """Turn a list back to front, in place.

    BOTH RIGHT-HAND SPLITS BUILD BACKWARDS, because walking from the end is
    the natural way to find the last separator first -- and both then need the
    pieces in reading order.
    """
    n: i64 = load(i64, offset(out, apy_q_n_offset()))
    items: ptr = ptr(load(u64, offset(out, apy_q_items_offset())))
    i: i64 = 0
    j: i64 = n - 1
    while i < j:
        a: u64 = load(u64, offset(items, i * apy_value_size()))
        b: u64 = load(u64, offset(items, j * apy_value_size()))
        store(u64, b, offset(items, i * apy_value_size()))
        store(u64, a, offset(items, j * apy_value_size()))
        i = i + 1
        j = j - 1
    return out


def apy_split_ws_of(s: ptr, maxsplit: i64, from_right: i64) -> ptr:
    """Split on RUNS of whitespace, dropping the empty ends.

    THE REMAINDER GOES IN WHOLE once the limit is reached, INCLUDING its
    trailing whitespace: `"  a  b  ".split(None, 1)` is ["a", "b  "]. Only the
    whitespace BEFORE a piece is skipped. Right-stripping the remainder as
    well looks tidier and answers ["a", "b"], which is wrong -- and invisible
    unless a case splits a string that has trailing space.

    A `going` FLAG RATHER THAN A `break`, which the subset has none of. Using
    the loop variable as the stop condition would work here and not in the
    right-hand walk below, so both are written the same way.
    """
    out: ptr = apy_seq_new_of(apy_list_kind(), 8)
    if not out:
        return out
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    if not from_right:
        i: i64 = 0
        going: i64 = 1
        while going:
            while i < n and apy_c_space(i64(load(u8, offset(p, i)))) != 0:
                i = i + 1
            if i >= n:
                going = 0
            else:
                if maxsplit >= 0 and load(
                        i64, offset(out, apy_q_n_offset())) == maxsplit:
                    apy_q_append_of(out, apy_str_slice_of(s, i, n))
                    going = 0
                else:
                    j: i64 = i
                    while j < n and apy_c_space(
                            i64(load(u8, offset(p, j)))) == 0:
                        j = j + 1
                    apy_q_append_of(out, apy_str_slice_of(s, i, j))
                    i = j
        return out
    # THE MIRROR IMAGE: the remainder keeps its LEADING whitespace, so
    # `"  a  b  ".rsplit(None, 1)` is ["  a", "b"].
    k: i64 = n
    walking: i64 = 1
    while walking:
        while k > 0 and apy_c_space(i64(load(u8, offset(p, k - 1)))) != 0:
            k = k - 1
        if k <= 0:
            walking = 0
        else:
            if maxsplit >= 0 and load(
                    i64, offset(out, apy_q_n_offset())) == maxsplit:
                apy_q_append_of(out, apy_str_slice_of(s, 0, k))
                walking = 0
            else:
                m: i64 = k
                while m > 0 and apy_c_space(
                        i64(load(u8, offset(p, m - 1)))) == 0:
                    m = m - 1
                apy_q_append_of(out, apy_str_slice_of(s, m, k))
                k = m
    return apy_seq_reverse_of(out)


def apy_split_sep_of(s: ptr, sep: ptr, maxsplit: i64,
                     from_right: i64) -> ptr:
    """Split on each occurrence of `sep`, keeping the empty pieces.

    THE LAST PIECE IS APPENDED OUTSIDE THE LOOP, which is why `"a,".split(",")`
    is ["a", ""] and not ["a"]: the text after the final separator is a piece
    even when it is empty, and so is the whole string when there was no
    separator at all.

    AN EMPTY SEPARATOR IS REFUSED, because every position would match and the
    loop would not advance -- Python refuses it for the same reason.
    """
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    m: i64 = load(i64, offset(sep, apy_str_len_offset()))
    if m == 0:
        return apy_raise_fmt(rodata(b"ValueError\0"),
                             rodata(b"empty separator%s%s\0"),
                             rodata(b"\0"), rodata(b"\0"))
    out: ptr = apy_seq_new_of(apy_list_kind(), 8)
    if not out:
        return out
    if not from_right:
        i: i64 = 0
        going: i64 = 1
        while going:
            if maxsplit >= 0 and load(
                    i64, offset(out, apy_q_n_offset())) >= maxsplit:
                going = 0
            else:
                at: i64 = apy_find_at(s, sep, i, n)
                if at < 0:
                    going = 0
                else:
                    apy_q_append_of(out, apy_str_slice_of(s, i, at))
                    i = at + m
        apy_q_append_of(out, apy_str_slice_of(s, i, n))
        return out
    k: i64 = n
    walking: i64 = 1
    while walking:
        if maxsplit >= 0 and load(
                i64, offset(out, apy_q_n_offset())) >= maxsplit:
            walking = 0
        else:
            back: i64 = apy_rfind_at(s, sep, 0, k)
            if back < 0:
                walking = 0
            else:
                apy_q_append_of(out, apy_str_slice_of(s, back + m, k))
                k = back
    apy_q_append_of(out, apy_str_slice_of(s, 0, k))
    return apy_seq_reverse_of(out)


def apy_split_limit() -> ptr:
    """One word: the `maxsplit` being read out of its argument."""
    return reserve("apy_split_limit_ir", 8)


def apy_str_split_impl_of(s: ptr, sep: ptr, limit: ptr,
                          from_right: i64) -> ptr:
    """Which of the two split algorithms this call means.

    NO SEPARATOR AND `None` ARE THE SAME THING and both mean whitespace, which
    is why the test admits either: `s.split()` and `s.split(None, 2)` are the
    same mode.

    ANY NEGATIVE LIMIT MEANS NO LIMIT, which is Python's rule -- `s.split(",",
    -3)` splits on every comma rather than refusing.

    BYTES TOO. `b"a,b".split(b",")` is the same operation on the same layout;
    the RECEIVER decides the result's kind, which `apy_str_like` settles above
    this.
    """
    maxsplit: i64 = -1
    if limit:
        bounds: ptr = apy_split_limit()
        store(i64, -1, bounds)
        if not apy_int_arg_of(limit, bounds):
            return ptr(0)
        maxsplit = load(i64, bounds)
    if maxsplit < 0:
        maxsplit = -1
    if not sep:
        return apy_split_ws_of(s, maxsplit, from_right)
    if i64(load(i32, offset(sep, 0))) == apy_none_kind():
        return apy_split_ws_of(s, maxsplit, from_right)
    k: i64 = i64(load(i32, offset(sep, 0)))
    if k != apy_str_kind() and k != apy_bytes_kind():
        return apy_raise_fmt(rodata(b"TypeError\0"),
                             rodata(b"must be str or None, not %s%s\0"),
                             apy_kind_name_of(sep), rodata(b"\0"))
    return apy_split_sep_of(s, sep, maxsplit, from_right)


def apy_str_split_ws(s: ptr) -> ptr:
    """`s.split()`."""
    if not apy_str_self_of(rodata(b"split\0"), s):
        return ptr(0)
    return apy_str_split_impl_of(s, ptr(0), ptr(0), 0)


def apy_str_split_n(s: ptr, sep: ptr, limit: ptr) -> ptr:
    """`s.split(sep, maxsplit)`."""
    if not apy_str_self_of(rodata(b"split\0"), s):
        return ptr(0)
    return apy_str_split_impl_of(s, sep, limit, 0)


def apy_str_rsplit_ws(s: ptr) -> ptr:
    """`s.rsplit()`."""
    if not apy_str_self_of(rodata(b"rsplit\0"), s):
        return ptr(0)
    return apy_str_split_impl_of(s, ptr(0), ptr(0), 1)


def apy_str_rsplit_n(s: ptr, sep: ptr, limit: ptr) -> ptr:
    """`s.rsplit(sep, maxsplit)`."""
    if not apy_str_self_of(rodata(b"rsplit\0"), s):
        return ptr(0)
    return apy_str_split_impl_of(s, sep, limit, 1)


def apy_splitlines_impl_of(s: ptr, keepends: i64) -> ptr:
    """`s.splitlines()` -- split on line boundaries.

    `\r\n` IS ONE BREAK AND NOT TWO, which is the whole reason this is not
    `split("\n")`: a file written on Windows would otherwise gain an empty
    line between every pair.

    A TRAILING BREAK ADDS NO EMPTY PIECE -- `"a\n".splitlines()` is `["a"]`
    where `"a\n".split("\n")` is `["a", ""]`. That falls out of the walk:
    the loop ends when the break is consumed, with nothing left to start a
    new piece.

    `keepends` PUTS THE BREAK BACK ON, which is what makes the pieces
    reassemble into the original.
    """
    out: ptr = apy_seq_new_of(apy_list_kind(), 8)
    if not out:
        return out
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    i: i64 = 0
    while i < n:
        start: i64 = i
        going: i64 = 1
        while going:
            if i >= n:
                going = 0
            else:
                c: i64 = i64(load(u8, offset(p, i)))
                if c == 13 or c == 10:
                    going = 0
                else:
                    i = i + 1
        stop: i64 = i
        if i < n:
            two: i64 = 0
            if i64(load(u8, offset(p, i))) == 13:
                if i + 1 < n:
                    if i64(load(u8, offset(p, i + 1))) == 10:
                        two = 1
            if two:
                i = i + 2
            else:
                i = i + 1
        cut: i64 = stop
        if keepends:
            cut = i
        apy_q_append_of(out, apy_str_slice_of(s, start, cut))
    return out


def apy_str_expandtabs(s: ptr, width: ptr) -> ptr:
    """`s.expandtabs(width)`.

    A TAB ADVANCES TO THE NEXT MULTIPLE OF `width`, which is not the same as
    inserting `width` spaces: the padding depends on the column reached so
    far, and that is what makes columns line up.

    THE COLUMN RESETS ON A LINE BREAK, so each line is tabulated from its own
    start rather than from the beginning of the string.

    THE BUFFER IS SIZED FOR EVERY BYTE BEING A TAB, which is the worst case
    and cheap in a bump arena.
    """
    if i64(load(i32, offset(s, 0))) != apy_str_kind():
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute "
                   b"'expandtabs'%s\0"),
            apy_kind_name_of(s), rodata(b"\0"))
    w: i64 = 8
    if apy_is_int_like_of(width):
        w = apy_int_payload(width)
    if w < 1:
        w = 1
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    cap: i64 = n * w + 8
    buf: ptr = apy_alloc_bytes(cap + 1)
    if not buf:
        return buf
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    col: i64 = 0
    out: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = i64(load(u8, offset(p, i)))
        if c == 9:
            pad: i64 = w - (col % w)
            while pad > 0 and out < cap:
                store(u8, u8(32), offset(buf, out))
                out = out + 1
                col = col + 1
                pad = pad - 1
        else:
            if out < cap:
                store(u8, u8(c), offset(buf, out))
                out = out + 1
            if c == 13 or c == 10:
                col = 0
            else:
                col = col + 1
        i = i + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)


def apy_to_bytes_n(v: ptr, length: ptr, order: ptr) -> ptr:
    """`n.to_bytes(length, order)`.

    ANYTHING LEFT OVER IS AN OVERFLOW, which is why the magnitude is checked
    AFTER the bytes are written rather than by counting bits first: the loop
    shifts the value away, and a non-zero remainder means it did not fit.

    BIG-ENDIAN IS THE DEFAULT HERE because anything that is not exactly the
    string "little" is treated as big -- the C's rule, kept.

    A NEGATIVE INT IS REFUSED because `signed=` is not supported; two's
    complement over an arbitrary width is a different function.
    """
    if not apy_is_int_like_of(v):
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute "
                   b"'to_bytes'%s\0"),
            apy_kind_name_of(v), rodata(b"\0"))
    if not apy_is_int_like_of(length):
        return apy_raise_at(
            rodata(b"TypeError\0"),
            rodata(b"to_bytes() length must be an integer\0"))
    n: i64 = apy_int_payload(length)
    if n < 0 or n > 1024:
        return apy_raise_at(rodata(b"OverflowError\0"),
                            rodata(b"int too big to convert\0"))
    big: i64 = 1
    if i64(load(i32, offset(order, 0))) == apy_str_kind():
        if apy_cstr_eq(ptr(load(u64, offset(order, apy_str_ptr_offset()))),
                       rodata(b"little\0")):
            big = 0
    if apy_int_payload(v) < 0:
        return apy_raise_at(
            rodata(b"OverflowError\0"),
            rodata(b"can't convert negative int to unsigned\0"))
    m: u64 = u64(apy_int_payload(v))
    room: i64 = n
    if room == 0:
        room = 1
    buf: ptr = apy_alloc_bytes(room + 1)
    if not buf:
        return buf
    z: i64 = 0
    while z <= room:
        store(u8, u8(0), offset(buf, z))
        z = z + 1
    i: i64 = 0
    while i < n:
        at: i64 = i
        if big:
            at = n - 1 - i
        store(u8, u8(i64(m & u64(255))), offset(buf, at))
        m = m >> u64(8)
        i = i + 1
    if m != u64(0):
        return apy_raise_at(rodata(b"OverflowError\0"),
                            rodata(b"int too big to convert\0"))
    return apy_bytes_literal(buf, n)


def apy_str_splitlines(s: ptr) -> ptr:
    """`s.splitlines()`."""
    if not apy_str_self_of(rodata(b"splitlines\0"), s):
        return ptr(0)
    return apy_splitlines_impl_of(s, 0)


def apy_str_splitlines_keep(s: ptr, keep: ptr) -> ptr:
    """`s.splitlines(keepends)`.

    ANY TRUTHY VALUE KEEPS THE ENDS, not just True -- `splitlines(1)` is what
    a program written against the C API spells, and Python accepts it.
    """
    if not apy_str_self_of(rodata(b"splitlines\0"), s):
        return ptr(0)
    return apy_splitlines_impl_of(s, apy_truth(keep))
