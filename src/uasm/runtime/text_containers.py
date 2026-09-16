# WHAT A CONTAINER LOOKS LIKE. `[1, 2]`, `{'a': 1}`, `{1, 2}`.
#
# THE RECURSION CROSSES THE SPLIT AND THAT IS FINE. Each of these renders its
# elements by calling `apy_text_of`, which is the ported half -- and for a
# kind that half does not answer yet, `apy_text_of_slow` takes it back to the
# C, whose own `apy_text` is a delegate to `apy_text_of` again. So a dict
# inside a list inside a set walks in and out of the port as many times as it
# needs to and comes back with the same text either way. THAT is what makes
# these three portable one at a time rather than as one batch: not one of
# them needs the others to be here.
#
# THE SHAPE IS THE SAME THREE TIMES: render every element first, adding up how
# much room the answer needs, then write the rendered parts into one buffer.
# RENDERING FIRST IS NOT AN OPTIMISATION -- an element's text has no known
# length until it exists, and growing the buffer instead would mean
# reallocating underneath text already written into it.
#
# THE PARTS ARRAY HOLDS VALUE HANDLES, not bytes. It is never freed, which the
# arena makes right: the C's `free(parts)` has no counterpart here because
# nothing in this runtime frees anything.


def apy_text_into(dst: ptr, at: i64, s: ptr) -> i64:
    """Copy one rendered string's bytes into `dst` at `at`. The new end."""
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    i: i64 = 0
    while i < n:
        store(u8, load(u8, offset(p, i)), offset(dst, at + i))
        i = i + 1
    return at + n


def apy_part_render(parts: ptr, items: ptr, i: i64) -> ptr:
    """Render element `i` into slot `i`, and answer it.

    ZERO MEANS AN ERROR IS SET and every caller checks. The C does not: it
    reads the length straight out of whatever came back, so a `__repr__` that
    raised took the process down through a null dereference instead of
    through the exception it had just set.
    """
    one: ptr = apy_text_of(
        ptr(load(u64, offset(items, i * apy_value_size()))), 1)
    if not one:
        return one
    store(u64, u64(one), offset(parts, i * apy_value_size()))
    return one


def apy_seq_text_of(v: ptr) -> ptr:
    """`[1, 2]` for a list, `(1, 2)` for a tuple.

    THE ONE-ELEMENT TUPLE KEEPS ITS COMMA, because `(1)` is not a tuple and
    reading that repr back would give an integer.

    ALREADY BEING RENDERED IS THE CYCLE, and Python writes `[...]` for it. A
    tuple cannot be BUILT holding itself but can be REACHED holding itself
    through a list, so both spellings are needed.
    """
    tup: i64 = 0
    if i64(load(i32, offset(v, 0))) == apy_tuple_kind():
        tup = 1
    if apy_repr_entered(v):
        if tup:
            return apy_from_cstr(rodata(b"(...)\0"))
        return apy_from_cstr(rodata(b"[...]\0"))
    n: i64 = load(i64, offset(v, apy_q_n_offset()))
    items: ptr = ptr(load(u64, offset(v, apy_q_items_offset())))
    room: i64 = n
    if room == 0:
        room = 1
    parts: ptr = apy_alloc_bytes(room * apy_value_size())
    if not parts:
        apy_repr_left(v)
        return parts
    length: i64 = 2
    i: i64 = 0
    while i < n:
        one: ptr = apy_part_render(parts, items, i)
        if not one:
            apy_repr_left(v)
            return one
        length = length + load(i64, offset(one, apy_str_len_offset())) + 2
        i = i + 1
    apy_repr_left(v)
    if tup:
        if n == 1:
            length = length + 1
    buf: ptr = apy_alloc_bytes(length + 1)
    if not buf:
        return buf
    if tup:
        store(u8, u8(40), buf)
    else:
        store(u8, u8(91), buf)
    out: i64 = 1
    i = 0
    while i < n:
        if i:
            store(u8, u8(44), offset(buf, out))
            store(u8, u8(32), offset(buf, out + 1))
            out = out + 2
        out = apy_text_into(
            buf, out, ptr(load(u64, offset(parts, i * apy_value_size()))))
        i = i + 1
    if tup:
        if n == 1:
            store(u8, u8(44), offset(buf, out))
            out = out + 1
    if tup:
        store(u8, u8(41), offset(buf, out))
    else:
        store(u8, u8(93), offset(buf, out))
    out = out + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)


def apy_dict_text_of(v: ptr) -> ptr:
    """`{'a': 1, 'b': 2}`.

    ONE PARTS ARRAY OF TWICE THE LENGTH rather than two arrays, so a key and
    its value sit next to each other and the writing loop picks its separator
    off the low bit of the index -- `:` after a key, `,` after a value. That
    is the C's trick and it is worth keeping: the two halves cannot get out
    of step with each other because there is only one step.

    `d['self'] = d` IS AS ORDINARY AS THE LIST VERSION and recursing on it
    runs the stack out, which is what the guard is for.
    """
    n: i64 = load(i64, offset(v, apy_d_n_offset()))
    if n == 0:
        return apy_from_cstr(rodata(b"{}\0"))
    if apy_repr_entered(v):
        return apy_from_cstr(rodata(b"{...}\0"))
    keys: ptr = ptr(load(u64, offset(v, apy_d_keys_offset())))
    vals: ptr = ptr(load(u64, offset(v, apy_d_vals_offset())))
    parts: ptr = apy_alloc_bytes(n * 2 * apy_value_size())
    if not parts:
        apy_repr_left(v)
        return parts
    length: i64 = 3
    i: i64 = 0
    while i < n:
        # NOT `apy_part_render`, which reads and writes the SAME index: a key
        # at `i` belongs in slot `i * 2` and its value in `i * 2 + 1`, so the
        # two indices come apart here and the helper cannot be used.
        k: ptr = apy_text_of(
            ptr(load(u64, offset(keys, i * apy_value_size()))), 1)
        if not k:
            apy_repr_left(v)
            return k
        store(u64, u64(k), offset(parts, i * 2 * apy_value_size()))
        w: ptr = apy_text_of(
            ptr(load(u64, offset(vals, i * apy_value_size()))), 1)
        if not w:
            apy_repr_left(v)
            return w
        store(u64, u64(w), offset(parts, (i * 2 + 1) * apy_value_size()))
        length = length + load(i64, offset(k, apy_str_len_offset()))
        length = length + load(i64, offset(w, apy_str_len_offset())) + 4
        i = i + 1
    apy_repr_left(v)
    buf: ptr = apy_alloc_bytes(length + 1)
    if not buf:
        return buf
    store(u8, u8(123), buf)
    out: i64 = 1
    i = 0
    while i < n * 2:
        if i:
            if i & 1:
                store(u8, u8(58), offset(buf, out))
            else:
                store(u8, u8(44), offset(buf, out))
            store(u8, u8(32), offset(buf, out + 1))
            out = out + 2
        out = apy_text_into(
            buf, out, ptr(load(u64, offset(parts, i * apy_value_size()))))
        i = i + 1
    store(u8, u8(125), offset(buf, out))
    out = out + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)


def apy_set_text_of(v: ptr) -> ptr:
    """`{1, 2}`, and `frozenset({1, 2})` for the immutable one.

    AN EMPTY SET IS `set()` AND NOT `{}`, which is the dict's spelling -- so
    the empty case is answered before anything else rather than being left to
    fall out of the loop.

    NO RECURSION GUARD, and none is needed: a set holds only hashable things,
    and a set is not one. The C does not guard this one either.
    """
    frozen: i64 = 0
    if i64(load(i32, offset(v, 0))) == apy_frozen_kind():
        frozen = 1
    n: i64 = load(i64, offset(v, apy_q_n_offset()))
    if n == 0:
        if frozen:
            return apy_from_cstr(rodata(b"frozenset()\0"))
        return apy_from_cstr(rodata(b"set()\0"))
    items: ptr = ptr(load(u64, offset(v, apy_q_items_offset())))
    parts: ptr = apy_alloc_bytes(n * apy_value_size())
    if not parts:
        return parts
    length: i64 = 3
    if frozen:
        length = 13
    i: i64 = 0
    while i < n:
        one: ptr = apy_part_render(parts, items, i)
        if not one:
            return one
        length = length + load(i64, offset(one, apy_str_len_offset())) + 2
        i = i + 1
    buf: ptr = apy_alloc_bytes(length + 1)
    if not buf:
        return buf
    out: i64 = 0
    if frozen:
        out = apy_cstr_into(buf, 0, length, rodata(b"frozenset(\0"))
    store(u8, u8(123), offset(buf, out))
    out = out + 1
    i = 0
    while i < n:
        if i:
            store(u8, u8(44), offset(buf, out))
            store(u8, u8(32), offset(buf, out + 1))
            out = out + 2
        out = apy_text_into(
            buf, out, ptr(load(u64, offset(parts, i * apy_value_size()))))
        i = i + 1
    store(u8, u8(125), offset(buf, out))
    out = out + 1
    if frozen:
        store(u8, u8(41), offset(buf, out))
        out = out + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)
