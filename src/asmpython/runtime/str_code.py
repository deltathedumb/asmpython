# `ord` and `chr`, written in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, the third str step and the first ported
# code that ALLOCATES A BUFFER and hands it to the rest of the runtime. Every
# earlier step either filled a cell the arena had already sized (`str_cell.py`)
# or only read one (`str_len.py`).
#
# WHY THAT MATTERS MORE THAN THE TWO FUNCTIONS DO. A bump-pointer arena cannot
# free, so the question every later kind runs into -- who owns the bytes, and
# what happens when nobody can give them back -- is answered here at the
# smallest possible scale: `chr` needs at most five bytes and they are
# immortal, which is exactly the case a bump pointer is right for.
#
# THE ERROR PATHS ARE DECLINED, NOT REIMPLEMENTED. `apy_fail` is not reachable
# from the subset, and that turns out to be the right shape anyway: the fast
# path handles the cases that ARE a character or ARE a code point, and hands
# everything else to the C, which already owns the wording of five different
# messages. A fast path that had to be total could not begin -- the same
# argument `int_arith.py` makes about `apy_add` being polymorphic over
# eighteen kinds.


def apy_utf8_width(lead: i64) -> i64:
    """How many bytes the sequence starting with `lead` occupies.

    ZERO IS NOT A WIDTH, it is "this is not a lead byte" -- a stray
    continuation, which the C treats as a one-byte character with the raw
    value. This answers 1 for it too, and the CALLER decides: `apy_ord`
    compares the width against the whole length, so a malformed string is
    simply not one character and declines.
    """
    if lead < 128:
        return 1
    if (lead & 224) == 192:
        return 2
    if (lead & 240) == 224:
        return 3
    if (lead & 248) == 240:
        return 4
    return 1


def apy_utf8_lead_bits(lead: i64, width: i64) -> i64:
    """The payload bits a lead byte contributes, by width."""
    if width == 1:
        return lead
    if width == 2:
        return lead & 31
    if width == 3:
        return lead & 15
    return lead & 7


def apy_ord(v: ptr) -> ptr:
    """`ord(s)` -- the code point of a one-CHARACTER string.

    ONE CHARACTER, NOT ONE BYTE, and the two stopped coinciding when `chr`
    learned to build a multi-byte one. The length test counts a sequence and
    the answer DECODES it; testing bytes made `ord(chr(233))` a TypeError
    about a string of length != 1, describing a string the program never
    wrote. The C carries that comment and this has to keep its bargain.
    """
    if apy_is_bytes(v):
        if apy_str_byte_len(v) == 1:
            p: ptr = ptr(load(u64, offset(v, apy_str_ptr_offset())))
            return apy_from_int(i64(load(u8, p)))
        return apy_ord_slow(v)
    if not apy_is_str(v):
        return apy_ord_slow(v)
    n: i64 = apy_str_byte_len(v)
    if n < 1:
        return apy_ord_slow(v)
    at: ptr = ptr(load(u64, offset(v, apy_str_ptr_offset())))
    lead: i64 = i64(load(u8, at))
    width: i64 = apy_utf8_width(lead)
    # NOT ONE CHARACTER, so it is not this function's business. A truncated
    # sequence, a string of two characters and an empty one all arrive here.
    if n != width:
        return apy_ord_slow(v)
    code: i64 = apy_utf8_lead_bits(lead, width)
    i: i64 = 1
    while i < width:
        code = (code << 6) | (i64(load(u8, offset(at, i))) & 63)
        i = i + 1
    return apy_from_int(code)


def apy_chr(v: ptr) -> ptr:
    """`chr(i)` -- the one-character string for a code point.

    UTF-8, because that is how a str is stored here, so a code point becomes
    one to four bytes and `len` counts characters by decoding them again.

    THE TRAILING NUL IS WRITTEN and is not part of the length. Two hundred
    places in the remaining C read `v.s.p` as a C string -- `APY_CSTR`,
    `strcmp`, `snprintf` -- so a cell built without a terminator is a cell the
    rest of the runtime reads off the end of.

    BOOL DECLINES. `chr(True)` is `chr(1)` in Python and a bool is a different
    kind here; letting it through would be right for the value and wrong for
    nothing else, which is the worst kind of nearly-right. The C knows the
    whole rule, so the fast path takes exact ints and lets the rest go.
    """
    if not apy_is_int(v):
        return apy_chr_slow(v)
    code: i64 = apy_int_payload(v)
    if code < 0 or code > 1114111:
        return apy_chr_slow(v)
    n: i64 = 1
    if code >= 128:
        n = 2
    if code >= 2048:
        n = 3
    if code >= 65536:
        n = 4
    buf: ptr = apy_alloc_bytes(n + 1)
    if not buf:
        return buf
    if n == 1:
        store(u8, u8(code), buf)
    elif n == 2:
        store(u8, u8(192 | (code >> 6)), buf)
        store(u8, u8(128 | (code & 63)), offset(buf, 1))
    elif n == 3:
        store(u8, u8(224 | (code >> 12)), buf)
        store(u8, u8(128 | ((code >> 6) & 63)), offset(buf, 1))
        store(u8, u8(128 | (code & 63)), offset(buf, 2))
    else:
        store(u8, u8(240 | (code >> 18)), buf)
        store(u8, u8(128 | ((code >> 12) & 63)), offset(buf, 1))
        store(u8, u8(128 | ((code >> 6) & 63)), offset(buf, 2))
        store(u8, u8(128 | (code & 63)), offset(buf, 3))
    store(u8, u8(0), offset(buf, n))
    return apy_from_bytes(buf, n)
