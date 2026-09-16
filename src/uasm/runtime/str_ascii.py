# `ascii(x)`, and the permissive UTF-8 step it walks with.
#
# TWO UTF-8 READERS, AND THE DIFFERENCE IS THE POINT. `apy_utf8_step_of` next
# door VALIDATES -- it rejects overlong forms, surrogates and out-of-range
# code points by answering zero -- because it serves `repr`, which must not
# claim a string holds a character it does not. This one is PERMISSIVE: a
# byte it cannot lead with is returned as itself with a width of one, and a
# truncated sequence at the end of the string is too.
#
# WHICH IS RIGHT FOR `ascii`, whose whole job is to leave nothing above 0x7F
# in the output. Refusing to decode a bad byte would mean either dropping it
# or emitting it raw, and both are worse than naming it: `\xff` says exactly
# what is there.


def apy_utf8_at_of(p: ptr, n: i64, i: i64, lenp: ptr) -> i64:
    """The code point at byte `i`, writing its width through `lenp`.

    NEVER FAILS, which is what separates it from `apy_utf8_step_of`. Every
    byte has an answer: a stray continuation byte, a lead byte with too few
    bytes behind it, and a byte that leads nothing at all each come back as
    themselves with a width of one.

    NO OVERLONG CHECK AND NO SURROGATE CHECK, deliberately -- see the header.

    THE OUT-PARAMETER IS A PLAIN WORD, because the subset has one integer
    width and the C's `int64_t *` is not a type it can describe. The C's
    delegate does the cast, which is the whole reason the delegate exists.
    """
    c: i64 = i64(load(u8, offset(p, i)))
    want: i64 = 0
    code: i64 = 0
    if c < 128:
        want = 1
        code = c
    elif (c & 224) == 192:
        want = 2
        code = c & 31
    elif (c & 240) == 224:
        want = 3
        code = c & 15
    elif (c & 248) == 240:
        want = 4
        code = c & 7
    else:
        store(i64, 1, lenp)
        return c
    if i + want > n:
        store(i64, 1, lenp)
        return c
    k: i64 = 1
    while k < want:
        code = (code << 6) | (i64(load(u8, offset(p, i + k))) & 63)
        k = k + 1
    store(i64, want, lenp)
    return code


def apy_ascii_len() -> ptr:
    """One word, for the width `apy_utf8_at_of` writes back."""
    return reserve("apy_ascii_len_ir", 8)


def apy_ascii(v: ptr) -> ptr:
    """`ascii(x)` -- the repr, with everything above 0x7F escaped.

    IT IS `repr` FIRST AND THEN A PASS OVER THE RESULT, not a second renderer:
    `ascii` differs from `repr` only in what it will let through, so building
    it any other way would mean two things that must agree about quoting,
    escaping and every container's punctuation.

    TEN BYTES IS THE WIDEST ESCAPE -- `\\UNNNNNNNN` -- and one input byte can
    never produce more than one escape, so ten times the input plus a
    terminator cannot be exceeded.
    """
    shown: ptr = apy_repr(v)
    if not shown:
        return shown
    n: i64 = load(i64, offset(shown, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(shown, apy_str_ptr_offset())))
    out: ptr = apy_alloc_bytes(n * 10 + 1)
    if not out:
        return out
    slot: ptr = apy_ascii_len()
    at: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = i64(load(u8, offset(p, i)))
        if c < 128:
            store(u8, u8(c), offset(out, at))
            at = at + 1
            i = i + 1
        else:
            code: i64 = apy_utf8_at_of(p, n, i, slot)
            if code < 256:
                store(u8, u8(92), offset(out, at))
                store(u8, u8(120), offset(out, at + 1))
                at = apy_hex_into(out, at + 2, code, 2)
            elif code < 65536:
                store(u8, u8(92), offset(out, at))
                store(u8, u8(117), offset(out, at + 1))
                at = apy_hex_into(out, at + 2, code, 4)
            else:
                store(u8, u8(92), offset(out, at))
                store(u8, u8(85), offset(out, at + 1))
                at = apy_hex_into(out, at + 2, code, 8)
            i = i + load(i64, slot)
    store(u8, u8(0), offset(out, at))
    return apy_from_bytes(out, at)


def apy_hex_shortest(dst: ptr, at: i64, value: i64) -> i64:
    """`value` in lowercase hex, no leading zeros. The new end.

    NOT `apy_hex_into`, which pads to a fixed width because an escape has to
    read back as what it was. An ADDRESS has no such requirement and CPython
    writes it short -- `0x1f4` and not `0x00000000000001f4`.

    ZERO IS THE ONE CASE THE LOOP CANNOT WRITE, since it has no highest set
    digit to start from, and it is written out rather than special-cased in
    the loop's bound.
    """
    if value == 0:
        store(u8, u8(48), offset(dst, at))
        return at + 1
    top: i64 = 15
    while ((value >> (top * 4)) & 15) == 0:
        top = top - 1
    return apy_hex_into(dst, at, value, top + 1)


def apy_default_repr(v: ptr) -> ptr:
    """`<Name object at 0x...>` -- what an instance prints without a `__repr__`.

    THE ADDRESS IS PRINTED AND NOT OMITTED even though no two runs agree on
    it, so no conformance case can assert one. A program that prints a bare
    instance is telling its reader it never defined `__repr__`, and hiding
    the address would hide that.

    ANYTHING THAT IS NOT AN INSTANCE FALLS BACK TO ITS ORDINARY REPR, which
    is what makes this safe to reach for as `object.__repr__`.
    """
    if i64(load(i32, offset(v, 0))) != apy_inst_kind():
        return apy_repr(v)
    name: ptr = ptr(load(u64, offset(
        ptr(load(u64, offset(
            ptr(load(u64, offset(v, apy_o_cls_offset()))),
            apy_t_name_offset()))),
        apy_str_ptr_offset())))
    room: i64 = apy_cstr_len(name) + 40
    buf: ptr = apy_alloc_bytes(room)
    if not buf:
        return buf
    out: i64 = apy_cstr_into(buf, 0, room, rodata(b"<\0"))
    out = apy_cstr_into(buf, out, room, name)
    out = apy_cstr_into(buf, out, room, rodata(b" object at 0x\0"))
    out = apy_hex_shortest(buf, out, i64(u64(v)))
    store(u8, u8(62), offset(buf, out))
    out = out + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)
