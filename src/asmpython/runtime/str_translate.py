# `s.translate(table)` -- a per-CHARACTER substitution.
#
# BY CODE POINT AND NOT BY BYTE, which is what the table is keyed on:
# `str.maketrans` builds `{ord(c): ...}`, so stepping a byte at a time would
# look up 'e' under each half of its encoding and match neither.
#
# A REPLACEMENT MAY BE A WHOLE STRING, not just one character --
# `{ord('&'): 'and'}` is an ordinary thing to write -- and it may be `None`,
# which DELETES. So the output length is not a function of the input length
# and cannot be guessed.
#
# TWO PASSES RATHER THAN A GROWING BUFFER. The C reallocs when a long
# replacement overruns its estimate; the arena has no realloc and does not
# want one, so the first pass adds up exactly how much room the answer needs
# and the second writes it. The lookups happen twice, which is the price, and
# it buys an exact allocation and no copying.


def apy_translate_slot() -> ptr:
    """One word, for the width `apy_utf8_at_of` writes back."""
    return reserve("apy_translate_slot_ir", 8)


def apy_translate_to(table: ptr, cp: i64) -> ptr:
    """What `cp` maps to, or null for "not in the table -- keep it"."""
    at: i64 = apy_dict_find_of(table, apy_from_int(cp))
    if at < 0:
        return ptr(0)
    return ptr(load(u64, offset(
        ptr(load(u64, offset(table, apy_d_vals_offset()))),
        at * apy_value_size())))


def apy_translate_width(to: ptr) -> i64:
    """How many bytes a replacement contributes. -1 if it is not a legal one.

    `None` IS ZERO AND NOT AN ERROR: that is how a table spells a deletion.
    """
    if i64(load(i32, offset(to, 0))) == apy_none_kind():
        return 0
    if apy_is_int_like_of(to):
        ch: ptr = apy_chr(to)
        if not ch:
            return -1
        return load(i64, offset(ch, apy_str_len_offset()))
    if i64(load(i32, offset(to, 0))) == apy_str_kind():
        return load(i64, offset(to, apy_str_len_offset()))
    return -1


def apy_str_translate(s: ptr, table: ptr) -> ptr:
    """`s.translate(table)`."""
    if not apy_str_self_of(rodata(b"translate\0"), s):
        return ptr(0)
    if i64(load(i32, offset(table, 0))) != apy_dict_kind():
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object is not subscriptable%s\0"),
            apy_kind_name_of(table), rodata(b"\0"))
    n: i64 = load(i64, offset(s, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    slot: ptr = apy_translate_slot()
    room: i64 = 0
    i: i64 = 0
    while i < n:
        cp: i64 = apy_utf8_at_of(p, n, i, slot)
        used: i64 = load(i64, slot)
        to: ptr = apy_translate_to(table, cp)
        if not to:
            room = room + used
        else:
            w: i64 = apy_translate_width(to)
            if w < 0:
                return apy_raise_at(
                    rodata(b"TypeError\0"),
                    rodata(b"character mapping must be in range(0x110000)\0"))
            room = room + w
        i = i + used
    buf: ptr = apy_alloc_bytes(room + 1)
    if not buf:
        return buf
    out: i64 = 0
    i = 0
    while i < n:
        cp2: i64 = apy_utf8_at_of(p, n, i, slot)
        used2: i64 = load(i64, slot)
        to2: ptr = apy_translate_to(table, cp2)
        if not to2:
            k: i64 = 0
            while k < used2:
                store(u8, load(u8, offset(p, i + k)), offset(buf, out))
                out = out + 1
                k = k + 1
        elif i64(load(i32, offset(to2, 0))) == apy_none_kind():
            out = out + 0
        elif apy_is_int_like_of(to2):
            made: ptr = apy_chr(to2)
            if not made:
                return made
            out = apy_text_into(buf, out, made)
        else:
            out = apy_text_into(buf, out, to2)
        i = i + used2
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)
