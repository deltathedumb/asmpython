# The `str.is*` predicates, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the first port that moves a WHOLE
# FAMILY rather than a function. The C reaches all twelve of these through one
# static worker, `apy_str_is`, which walks the string by code point and asks
# `apy_char_class` what each one is. Twelve exported wrappers, one body.
#
# THE WORKER CANNOT BE PORTED and that is the point. `apy_char_class` decides
# ASCII in code and everything above it by looking in `@UNICODE_TABLE@` -- a
# generated table of runs that this subset has no way to read. So the family
# is not ported by porting the thing they share; it is ported by writing the
# ASCII half of that thing once, here, and giving each wrapper the same
# decline.
#
# ── the split, and why ASCII is the right line to draw ─────────────────────
#
# ONE TEST DECIDES ALL TWELVE: is every byte below 0x80? If it is, the string
# is pure ASCII, no byte is part of a multi-byte sequence, and each byte IS a
# character -- so the walk-by-code-point that the C does so carefully collapses
# into a walk by byte, and `apy_char_class` collapses into `runtime/ascii.py`,
# which is already ported. If it is not, the answer depends on the table and
# the call goes back to the C untouched.
#
# THIS IS NOT THE BYTES-VERSUS-CHARACTERS BUG that `str_len.py` and
# `str_find.py` exist to fix. Those two had to be TAUGHT characters because
# they answer with a position, and a byte position is a wrong answer. This one
# does not walk multi-byte strings at all -- it hands them back. The fast path
# is exact because it only ever runs where the two walks agree.
#
# `isascii` IS THE EXCEPTION and never declines: "is every byte below 0x80" is
# not a precondition for its answer, it IS its answer. Its slow half exists
# only for a receiver that is not a string.
#
# THREE PREDICATES THAT ARE ONE HERE. `isdecimal`, `isdigit` and `isnumeric`
# are three different questions outside ASCII -- U+00B2 is a digit and numeric
# but not decimal, U+2167 is numeric alone -- and the C's comment says so, and
# its table tells them apart. Inside ASCII they are the same question, because
# the only characters in any of the three classes are 0..9. They are written
# as three functions rather than three names for one so that the difference
# stays visible where it matters: above 0x80, where none of them run.


def apy_str_ascii_len(s: ptr) -> i64:
    """The byte length of `s` if it is a pure-ASCII string, else -1.

    THE WHOLE SPLIT IS THIS FUNCTION. Every predicate below opens with it and
    hands off to the C when it answers -1, so the decline is written once and
    the twelve cannot drift apart in what they accept.

    -1 RATHER THAN A FLAG-AND-LENGTH PAIR, because the subset has no tuples
    and an out-parameter would need a stack slot at every call site. A length
    is never negative, so the sentinel cannot collide with an answer.
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


def apy_str_ascii_at(s: ptr, i: i64) -> i64:
    """Byte `i` of `s`, widened, for a string already known to be ASCII."""
    return i64(load(u8, offset(ptr(load(u64, offset(s, apy_str_ptr_offset()))),
                               i)))


# ── the eight that ask the same question of every character ────────────────
#
# EACH ONE IS A LOOP THAT CAN ONLY SAY NO. They differ in the predicate and in
# nothing else, and every one of them answers False for the empty string --
# which is Python's rule, not an accident of the loop running no times.


def apy_str_isalpha(s: ptr) -> ptr:
    """`s.isalpha()` -- every character a letter, and at least one."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isalpha_slow(s)
    if n == 0:
        return apy_from_bool(0)
    i: i64 = 0
    while i < n:
        if not apy_c_alpha(apy_str_ascii_at(s, i)):
            return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isdigit(s: ptr) -> ptr:
    """`s.isdigit()`. In ASCII the digits are exactly 0..9."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isdigit_slow(s)
    if n == 0:
        return apy_from_bool(0)
    i: i64 = 0
    while i < n:
        if not apy_c_digit(apy_str_ascii_at(s, i)):
            return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isdecimal(s: ptr) -> ptr:
    """`s.isdecimal()`. Differs from `isdigit` only above 0x80."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isdecimal_slow(s)
    if n == 0:
        return apy_from_bool(0)
    i: i64 = 0
    while i < n:
        if not apy_c_digit(apy_str_ascii_at(s, i)):
            return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isnumeric(s: ptr) -> ptr:
    """`s.isnumeric()`. Differs from the other two only above 0x80."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isnumeric_slow(s)
    if n == 0:
        return apy_from_bool(0)
    i: i64 = 0
    while i < n:
        if not apy_c_digit(apy_str_ascii_at(s, i)):
            return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isalnum(s: ptr) -> ptr:
    """`s.isalnum()` -- every character a letter or a digit."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isalnum_slow(s)
    if n == 0:
        return apy_from_bool(0)
    i: i64 = 0
    while i < n:
        c: i64 = apy_str_ascii_at(s, i)
        if not apy_c_alpha(c):
            if not apy_c_digit(c):
                return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isspace(s: ptr) -> ptr:
    """`s.isspace()` -- the six ASCII whitespace bytes, and nothing else."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isspace_slow(s)
    if n == 0:
        return apy_from_bool(0)
    i: i64 = 0
    while i < n:
        if not apy_c_space(apy_str_ascii_at(s, i)):
            return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isprintable(s: ptr) -> ptr:
    """`s.isprintable()` -- 0x20 through 0x7e, space included.

    THE EMPTY STRING IS PRINTABLE and it is the only predicate here for which
    that is true. Python's rule is "no character is unprintable", which an
    empty string satisfies vacuously; the other eleven ask for at least one
    character of some kind, which it cannot supply. The C had this wrong --
    one `n == 0` return served all twelve -- and the fix went in with this
    file.

    A SPACE IS PRINTABLE AND NO OTHER WHITESPACE IS, which is the one place
    this differs from "not a control character": 0x20 is in the range below
    and 0x09 is not, so tab is unprintable and space is not.
    """
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isprintable_slow(s)
    i: i64 = 0
    while i < n:
        c: i64 = apy_str_ascii_at(s, i)
        if c < 32:
            return apy_from_bool(0)
        if c > 126:
            return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)


def apy_str_isascii(s: ptr) -> ptr:
    """`s.isascii()` -- and this one never declines for a string.

    THE PRECONDITION IS THE ANSWER. Every other predicate here uses
    `apy_str_ascii_len` to find out whether it is allowed to decide; this one
    uses it to decide. A -1 means either "not ASCII" or "not a string", and
    only the second needs the C -- so the kind is tested separately.

    THE EMPTY STRING IS ASCII, which falls out of the loop in
    `apy_str_ascii_len` running no times rather than being special-cased.
    """
    if not apy_is_str(s):
        return apy_str_isascii_slow(s)
    if apy_str_ascii_len(s) < 0:
        return apy_from_bool(0)
    return apy_from_bool(1)


# ── the three that need to remember something ──────────────────────────────


def apy_str_islower(s: ptr) -> ptr:
    """`s.islower()` -- no upper-case character, and at least one lower.

    "AT LEAST ONE LOWER" IS THE HALF THAT IS EASY TO MISS. `'ab1'.islower()`
    is True and `'123'.islower()` is False, so a plain "every character is
    lower-case" answers the second one wrongly -- a digit is not a
    counter-example, it just is not evidence either.
    """
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_islower_slow(s)
    if n == 0:
        return apy_from_bool(0)
    cased: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = apy_str_ascii_at(s, i)
        if apy_c_upper(c):
            return apy_from_bool(0)
        if apy_c_lower(c):
            cased = 1
        i = i + 1
    return apy_from_bool(cased)


def apy_str_isupper(s: ptr) -> ptr:
    """`s.isupper()` -- the mirror of the function above."""
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isupper_slow(s)
    if n == 0:
        return apy_from_bool(0)
    cased: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = apy_str_ascii_at(s, i)
        if apy_c_lower(c):
            return apy_from_bool(0)
        if apy_c_upper(c):
            cased = 1
        i = i + 1
    return apy_from_bool(cased)


def apy_str_istitle(s: ptr) -> ptr:
    """`s.istitle()` -- upper only after uncased, lower only after cased.

    THE RULE IS ABOUT NEIGHBOURS, not about characters, which is why this one
    carries state the other eleven do not. `'Ab Cd'` is title-case; `'AB'` is
    not, because the second `B` follows a cased character; `'aB'` is not,
    because the `a` follows nothing.

    AN UNCASED CHARACTER RESETS the run rather than breaking it, so `'A1b'` is
    False -- the `b` follows the uncased `1` -- and `'A1 B'` is True.
    """
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_istitle_slow(s)
    if n == 0:
        return apy_from_bool(0)
    cased: i64 = 0
    prev: i64 = 0
    i: i64 = 0
    while i < n:
        c: i64 = apy_str_ascii_at(s, i)
        if apy_c_upper(c):
            if prev:
                return apy_from_bool(0)
            cased = 1
            prev = 1
        elif apy_c_lower(c):
            if not prev:
                return apy_from_bool(0)
            cased = 1
            prev = 1
        else:
            prev = 0
        i = i + 1
    return apy_from_bool(cased)


def apy_str_isidentifier(s: ptr) -> ptr:
    """`s.isidentifier()` -- a letter or underscore, then letters, digits
    and underscores.

    XID_START AND XID_CONTINUE ARE THE REAL CLASSES and the C looks them up;
    inside ASCII they are exactly this. A leading digit is the only way the
    first character differs from the rest, and the underscore is in both.

    A KEYWORD IS AN IDENTIFIER by this method's definition -- `'if'` answers
    True -- which is Python's rule and not an oversight here.
    """
    n: i64 = apy_str_ascii_len(s)
    if n < 0:
        return apy_str_isidentifier_slow(s)
    if n == 0:
        return apy_from_bool(0)
    first: i64 = apy_str_ascii_at(s, 0)
    if not apy_c_alpha(first):
        if first != 95:
            return apy_from_bool(0)
    i: i64 = 1
    while i < n:
        c: i64 = apy_str_ascii_at(s, i)
        if not apy_c_alpha(c):
            if not apy_c_digit(c):
                if c != 95:
                    return apy_from_bool(0)
        i = i + 1
    return apy_from_bool(1)
