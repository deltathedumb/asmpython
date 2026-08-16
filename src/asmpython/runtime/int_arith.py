# Integer arithmetic: the fast path, in the machine subset.
#
# STAGE 3 OF docs/INERT-RUNTIME.md, second half. `int_cell.py` ported the
# CONSTRUCTOR; this ports the operations, and it is the first code here that
# has to coexist with the C rather than replace it.
#
# WHY A SPLIT RATHER THAN A PORT. `apy_add` is polymorphic over eighteen kinds
# -- int, float, bool, str, list, tuple, big, complex, an instance with
# `__add__` -- so porting it whole means porting all of them, which is the
# all-or-nothing this document exists to avoid. So the subset defines `apy_add`,
# answers the case it knows, and hands everything else to `apy_add_slow`, which
# is the C body under a new name (`link/objects.split_c`).
#
# THE C'S OWN CALLERS REACH THIS. Only the definition was renamed, so the
# hundred-odd `apy_add(...)` inside the runtime now enter here first. That is
# deliberate: a port that only accelerated the frontend's calls would leave
# most of the work where it was.
#
# THE LAYOUT HELPERS COME FROM `int_cell.py`. The runtime compiles as ONE
# unit, so its files call each other by name -- which is what keeps the
# cell's shape written down once.
#
# THIS IS THE SHAPE EVERY REMAINING KIND TAKES. str, list, dict and the rest
# each land as a fast path over a shrinking C remainder, and stage 6 deletes
# the remainder when there is nothing left in it.


def apy_is_int(v: ptr) -> bool:
    """Exactly `int`, and NOT `bool`.

    `True + True` is 2 in Python, so a bool is addable -- but it is a
    different kind with a different repr, and `apy_add`'s answer for two of
    them is an int. Letting a bool through here would be right for the value
    and wrong for nothing else, which is the worst kind of nearly-right: the C
    already knows the whole rule, so the fast path declines and lets it.
    """
    return load(i32, offset(v, apy_kind_offset())) == i32(apy_int_kind())


def apy_int_payload(v: ptr) -> i64:
    return load(i64, offset(v, apy_payload_offset()))


# ── the operations ──────────────────────────────────────────────────────────
#
# EACH OVERFLOW TEST IS EXACT for add and sub, and CONSERVATIVE for mul. A fast
# path is allowed to decline; it is not allowed to answer wrongly. Python's
# integers are arbitrary precision, so an overflow here is not an error but a
# promotion to the big-integer path, which is `apy_add_slow`'s job and stays
# entirely in C.

def apy_add(a: ptr, b: ptr) -> ptr:
    if apy_is_int(a) and apy_is_int(b):
        x: i64 = apy_int_payload(a)
        y: i64 = apy_int_payload(b)
        r: i64 = x + y
        # OVERFLOW IFF BOTH OPERANDS DISAGREE WITH THE RESULT. Two positives
        # cannot make a negative and two negatives cannot make a positive; a
        # mixed pair cannot overflow at all. `(x ^ r) & (y ^ r) < 0` is that
        # sentence, and it needs no wider type to evaluate -- which matters,
        # because there is no wider type.
        if ((x ^ r) & (y ^ r)) >= 0:
            return apy_from_int(r)
    return apy_add_slow(a, b)


def apy_sub(a: ptr, b: ptr) -> ptr:
    if apy_is_int(a) and apy_is_int(b):
        x: i64 = apy_int_payload(a)
        y: i64 = apy_int_payload(b)
        r: i64 = x - y
        # The operands must DIFFER in sign for a subtraction to overflow, and
        # then the result disagrees with the first. Same shape as add, one
        # operand negated -- and written out rather than expressed as
        # `apy_add(x, -y)`, because -y overflows on the most negative value.
        if ((x ^ y) & (x ^ r)) >= 0:
            return apy_from_int(r)
    return apy_sub_slow(a, b)


def apy_mul(a: ptr, b: ptr) -> ptr:
    if apy_is_int(a) and apy_is_int(b):
        x: i64 = apy_int_payload(a)
        y: i64 = apy_int_payload(b)
        # CONSERVATIVE, and deliberately so. The exact test is "divide the
        # product back and compare", and division here is PYTHON's -- it
        # floors, so the check is wrong for a negative operand in a way that
        # would only show on some of them. Two 32-bit operands cannot overflow
        # a 64-bit product, which is a test with no division in it and no
        # signedness to get wrong.
        #
        # Everything outside that band goes to the C, which is what a fast
        # path declining is supposed to look like. It costs the fast path on
        # large multiplications and gets the answer right on all of them.
        if apy_fits_i32(x) and apy_fits_i32(y):
            return apy_from_int(x * y)
    return apy_mul_slow(a, b)


def apy_fits_i32(v: i64) -> bool:
    return v >= -2147483648 and v <= 2147483647
