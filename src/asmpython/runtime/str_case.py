# The six `str` case transforms, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the second family to move on one test.
# `runtime/str_is.py` split twelve predicates by asking whether a string is
# pure ASCII; these six split on the same question, and the answer means the
# same thing: below 0x80 every byte IS a character, so the walk the C does by
# code point and the walk this does by byte cannot disagree.
#
# THE DIFFERENCE FROM THE PREDICATES IS THAT THESE BUILD SOMETHING. A
# predicate reads a string and answers a bool; a transform has to produce a
# new one, which means the arena, and which is why this family waited for
# `blocks.py` and `apy_str_copy_bytes` to be ported ahead of it. It is the
# first ported family that ALLOCATES.
#
# ── the length is the input's length, and only in ASCII ────────────────────
#
# The C asks for `n * 2 + 1` bytes because ONE CHARACTER CAN GROW: 'ß' has no
# single uppercase form, so `'ß'.upper()` is 'SS' and `casefold` gives 'ss' so
# the two match caselessly. That is a two-byte input becoming two bytes of a
# different shape, and in `capitalize` it can be a one-character word becoming
# two -- so the C cannot know the output length in advance and doubles.
#
# INSIDE ASCII NOTHING GROWS. Every transform below is a byte becoming a byte,
# so `n + 1` is exact rather than a guess, and the cell is built by the same
# `apy_str_copy_bytes` every other string in this runtime goes through.
#
# WHAT IS DECLINED, and it is more than the table. The C's worker also
# handles LATIN-1 IN CODE -- 0xC3 then a low byte, uppercase 0x80..0x9E and
# lowercase 0xA0..0xBE, offset by 0x20 exactly as ASCII is by 32. That branch
# needs no table and could be written here. It is not, because it is the case
# that can change the length, and a fast path that is right about 'é' and
# wrong about 'ß' would be worse than one that declines both: the second
# fails visibly at the gate, the first fails in the answer.


def apy_str_case_gate(s: ptr) -> i64:
    """The byte length of `s` if these transforms may run on it, else -1.

    THE SAME GATE AS `runtime/str_is.py` and deliberately a separate function
    rather than a shared one: that file's version answers "may I read this
    byte-wise", this one answers "may I read AND write this byte-wise", and
    the two happen to have the same body only because ASCII in equals ASCII
    out. The Latin-1 branch described above would change this one and not
    that one.
    """
    if not apy_is_str(s):
        return -1
    n: i64 = apy_str_byte_len(s)
    p: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    i: i64 = 0
    while i < n:
        if load(u8, offset(p, i)) > u8(127):
            return -1
        i = i + 1
    return n


def apy_str_case_build(s: ptr, n: i64, mode: i64) -> ptr:
    """`n` ASCII bytes of `s`, transformed by `mode`, as a new string.

    ONE BODY FOR SIX TRANSFORMS, as the C has, because they differ in a
    handful of comparisons and agree about everything else -- the allocation,
    the walk, the terminator and the cell. Six copies of that would be six
    places to fix a bug in the arena handling.

    THE MODES ARE THE C's, in its order: 0 upper, 1 lower, 2 title,
    3 capitalize, 4 swapcase, 5 casefold. They are numbers because the subset
    has no enum, and the six callers below are the only things that pass
    them.

    `casefold` IS `lower` HERE and that is not a shortcut: the pair casefold
    exists for is 'ß' against 'ss', which lives in the Latin-1 branch this
    gate declines. Within ASCII the two functions genuinely agree.
    """
    buf: ptr = apy_alloc_bytes(n + 1)
    if not buf:
        return buf
    src: ptr = ptr(load(u64, offset(s, apy_str_ptr_offset())))
    prev_cased: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = i64(load(u8, offset(src, i)))
        out: i64 = c
        if mode == 0:
            if apy_c_lower(c):
                out = c - 32
        elif mode == 1 or mode == 5:
            if apy_c_upper(c):
                out = c + 32
        elif mode == 4:
            if apy_c_lower(c):
                out = c - 32
            elif apy_c_upper(c):
                out = c + 32
        elif mode == 3:
            # ONLY THE FIRST CHARACTER IS RAISED and the whole rest lowered:
            # `'hello World'.capitalize()` is 'Hello world', not 'Hello
            # World'. The second half is the part that is easy to leave out.
            if i == 0:
                if apy_c_lower(c):
                    out = c - 32
            elif apy_c_upper(c):
                out = c + 32
        else:
            # `title` TRACKS THE PREVIOUS CHARACTER, which is why `'a1b'`
            # titles to 'A1B' and `"don't"` to "Don'T": a digit and an
            # apostrophe are both uncased, so the letter after either starts
            # a new word. Splitting on spaces disagrees with the first;
            # splitting on non-alphanumerics disagrees with the second.
            if prev_cased:
                if apy_c_upper(c):
                    out = c + 32
            elif apy_c_lower(c):
                out = c - 32
        prev_cased = apy_c_alpha(c)
        store(u8, u8(out), offset(buf, i))
        i = i + 1
    store(u8, u8(0), offset(buf, n))
    return apy_from_bytes(buf, n)


# ── the six ────────────────────────────────────────────────────────────────
#
# EACH IS THE GATE AND A MODE. An empty string needs no special case: the
# walk runs no times and the answer is the empty string, which is what Python
# gives for every one of these.


def apy_str_upper(s: ptr) -> ptr:
    """`s.upper()`."""
    n: i64 = apy_str_case_gate(s)
    if n < 0:
        return apy_str_upper_slow(s)
    return apy_str_case_build(s, n, 0)


def apy_str_lower(s: ptr) -> ptr:
    """`s.lower()`."""
    n: i64 = apy_str_case_gate(s)
    if n < 0:
        return apy_str_lower_slow(s)
    return apy_str_case_build(s, n, 1)


def apy_str_title(s: ptr) -> ptr:
    """`s.title()`."""
    n: i64 = apy_str_case_gate(s)
    if n < 0:
        return apy_str_title_slow(s)
    return apy_str_case_build(s, n, 2)


def apy_str_capitalize(s: ptr) -> ptr:
    """`s.capitalize()`."""
    n: i64 = apy_str_case_gate(s)
    if n < 0:
        return apy_str_capitalize_slow(s)
    return apy_str_case_build(s, n, 3)


def apy_str_swapcase(s: ptr) -> ptr:
    """`s.swapcase()`."""
    n: i64 = apy_str_case_gate(s)
    if n < 0:
        return apy_str_swapcase_slow(s)
    return apy_str_case_build(s, n, 4)


def apy_str_casefold(s: ptr) -> ptr:
    """`s.casefold()` -- `lower` inside ASCII, and only inside it."""
    n: i64 = apy_str_case_gate(s)
    if n < 0:
        return apy_str_casefold_slow(s)
    return apy_str_case_build(s, n, 5)
