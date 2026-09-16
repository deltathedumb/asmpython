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
# is the C body under a new name (`objects/csource.split_c`).
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


def apy_eq(a: ptr, b: ptr) -> ptr:
    """`a == b`. The IR half of a split, and the narrowest one here.

    TWO PLAIN INTEGERS AND NOTHING ELSE. The C's `apy_eq` handles instances
    through `__eq__`, then falls into `apy_eq_raw`, which compares across the
    whole numeric tower and by content for every other kind. This takes the
    case where both operands are `APY_INT_K` -- neither a big, since a big is
    a separate kind, and neither a bool, since that is a separate kind too.

    WHY BOOL IS DECLINED even though `True == 1` is True: it is a different
    kind with the same payload, so the comparison would be right and the KIND
    CHECK would have to be looser to reach it. A looser check is how a fast
    path starts answering for things it was not told about, which the runtime
    has been bitten by before -- `apy_eq_raw` fell through to a numeric
    comparison that read a pointer for non-numbers, and `b"ab" == b"a" + b"b"`
    was False. Narrow and certain beats wide and nearly right.

    NO IDENTITY SHORTCUT. `a is b` implying `a == b` is false for floats:
    `x = float("nan"); x == x` is False in Python and must stay False. It
    would be safe for the two integer kinds this accepts, and it is left out
    because the next person to widen the kind check would inherit it.
    """
    if not apy_is_int(a):
        return apy_eq_slow(a, b)
    if not apy_is_int(b):
        return apy_eq_slow(a, b)
    if apy_int_payload(a) == apy_int_payload(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


# ── the ordering family ────────────────────────────────────────────────────
#
# `apy_lt` and its three relatives are one line each in the C -- every one
# calls `apy_cmp`, which is `static` and handles the numeric tower, strings,
# bytes, lists compared lexicographically, and a user `__lt__`. So all four
# split the same way and take the same case: two plain integers.
#
# FIVE NEARLY IDENTICAL FUNCTIONS, AND NOT ONE PARAMETERISED ONE. A single
# `apy_order(a, b, which)` would be shorter and would put a branch on `which`
# in the hottest comparison a program makes -- and the subset has no way to
# express a jump table, so it would compile to a chain of tests. Written out,
# each is a load, a load, a compare and a return.
#
# THE SAME NARROWNESS AS `apy_eq`, for the same reason: bool and float are
# separate kinds and go to the C, because widening the check is how a fast
# path starts answering for things it was not told about.


def apy_lt(a: ptr, b: ptr) -> ptr:
    """`a < b`, for two plain integers."""
    if not apy_is_int(a):
        return apy_lt_slow(a, b)
    if not apy_is_int(b):
        return apy_lt_slow(a, b)
    if apy_int_payload(a) < apy_int_payload(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


def apy_le(a: ptr, b: ptr) -> ptr:
    """`a <= b`, for two plain integers."""
    if not apy_is_int(a):
        return apy_le_slow(a, b)
    if not apy_is_int(b):
        return apy_le_slow(a, b)
    if apy_int_payload(a) <= apy_int_payload(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


def apy_gt(a: ptr, b: ptr) -> ptr:
    """`a > b`, for two plain integers."""
    if not apy_is_int(a):
        return apy_gt_slow(a, b)
    if not apy_is_int(b):
        return apy_gt_slow(a, b)
    if apy_int_payload(a) > apy_int_payload(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


def apy_ge(a: ptr, b: ptr) -> ptr:
    """`a >= b`, for two plain integers."""
    if not apy_is_int(a):
        return apy_ge_slow(a, b)
    if not apy_is_int(b):
        return apy_ge_slow(a, b)
    if apy_int_payload(a) >= apy_int_payload(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


def apy_ne(a: ptr, b: ptr) -> ptr:
    """`a != b`, for two plain integers.

    NOT `not apy_eq(...)`, even though it would be correct for this case.
    `apy_ne` is a SEPARATE dunder in Python -- a class may define `__ne__`
    without `__eq__` and mean something that is not the negation -- so the
    slow halves are genuinely different functions, and routing through the
    fast `apy_eq` here would make the two look related in a way the fallback
    paths are not.
    """
    if not apy_is_int(a):
        return apy_ne_slow(a, b)
    if not apy_is_int(b):
        return apy_ne_slow(a, b)
    if apy_int_payload(a) != apy_int_payload(b):
        return apy_from_bool(1)
    return apy_from_bool(0)


# ── the bitwise family ─────────────────────────────────────────────────────
#
# THREE OF THEM CANNOT OVERFLOW AT ALL, which makes them the simplest split in
# this file: `&`, `|` and `^` of two int64s are an int64, always. There is no
# result to check and no case where the answer needs a wider type -- unlike
# `+`, which needs the sign test above it.
#
# THE TWO SHIFTS ARE NOT HERE, and they are the interesting omission:
#
#   `a << b` GROWS. Python has no word size, so `1 << 200` is an exact
#   integer and this runtime answers it with a big -- a different kind, built
#   by code that is still in C. A fast path could test whether the result
#   fits and decline otherwise, and that test is `b < 64 and the shift is
#   reversible`, which is most of the work of doing the shift twice.
#
#   `a >> b` IS SAFE FOR THE VALUE and not for the COUNT. A negative count is
#   a ValueError, and a count past 63 is 0 or -1 rather than whatever the
#   machine's shift instruction does with it -- which on x86 is a shift by
#   `b & 63`, so `1 >> 64` would answer 1. That is a wrong number from a
#   correct-looking instruction, which is the failure this whole arrangement
#   is built to make impossible, so it is left to the C until it is written
#   with both guards.


def apy_bitand(a: ptr, b: ptr) -> ptr:
    """`a & b`, for two plain integers. Cannot overflow."""
    if apy_is_int(a) and apy_is_int(b):
        return apy_from_int(apy_int_payload(a) & apy_int_payload(b))
    return apy_bitand_slow(a, b)


def apy_bitor(a: ptr, b: ptr) -> ptr:
    """`a | b`, for two plain integers. Cannot overflow."""
    if apy_is_int(a) and apy_is_int(b):
        return apy_from_int(apy_int_payload(a) | apy_int_payload(b))
    return apy_bitor_slow(a, b)


def apy_bitxor(a: ptr, b: ptr) -> ptr:
    """`a ^ b`, for two plain integers. Cannot overflow."""
    if apy_is_int(a) and apy_is_int(b):
        return apy_from_int(apy_int_payload(a) ^ apy_int_payload(b))
    return apy_bitxor_slow(a, b)


def apy_neg(v: ptr) -> ptr:
    """`-v`, for a plain integer.

    ONE VALUE IS DECLINED and it is the whole reason this needs a guard:
    -(-9223372036854775808) does not fit an int64, and Python answers it
    exactly -- as a big, which is a different kind built by code still in C.
    Negating it here would wrap back to itself, so `-x == x` for one integer
    and nothing would say why.
    """
    if apy_is_int(v):
        x: i64 = apy_int_payload(v)
        if x != -9223372036854775808:
            return apy_from_int(-x)
    return apy_neg_slow(v)


# ── floor division and remainder ───────────────────────────────────────────
#
# THE CORRECTION IS ALREADY PAID FOR, and that is why these are three lines
# rather than ten. Python's `//` floors toward negative infinity and its `%`
# takes the divisor's sign; a machine divides toward zero and takes the
# dividend's. Every other language writing this fast path has to add the
# fix-up -- `if (r != 0 && (r < 0) != (b < 0)) q -= 1` -- and this one does
# not, because the SUBSET IS PYTHON: the frontend lowers `//` to a truncating
# division plus that correction, once, for every program it compiles.
#
# So `x // y` below means what `a // b` means in the program being compiled.
# That is the whole argument of `docs/INERT-RUNTIME.md` for writing the
# runtime in this subset, arriving as three lines that did not have to be
# written.
#
# `apy_truediv` IS NOT HERE. `/` on two ints answers a float, and Python's is
# correctly rounded for values that do not fit a double exactly -- converting
# both to f64 and dividing is right for small operands and quietly wrong for
# large ones, which is the shape of error this runtime is built to refuse.


def apy_floordiv(a: ptr, b: ptr) -> ptr:
    """`a // b`, for two plain integers.

    TWO GUARDS, AND EACH IS A DIFFERENT KIND OF WRONG WITHOUT IT. A zero
    divisor is a ZeroDivisionError, whose message and error flag are the C's.
    And `-9223372036854775808 // -1` is 2**63, which does not fit -- Python
    answers it exactly as a big, so it goes to the C rather than wrapping back
    to itself here.
    """
    if apy_is_int(a) and apy_is_int(b):
        y: i64 = apy_int_payload(b)
        if y != 0:
            x: i64 = apy_int_payload(a)
            if x != -9223372036854775808 or y != -1:
                return apy_from_int(x // y)
    return apy_floordiv_slow(a, b)


def apy_mod(a: ptr, b: ptr) -> ptr:
    """`a % b`, for two plain integers.

    `b == -1` ANSWERS ZERO WITHOUT DIVIDING, as the C does. The value is
    right for every dividend, and taking it early is what keeps
    `-9223372036854775808 % -1` away from a machine remainder instruction --
    which on x86 traps rather than answering, because the quotient it would
    also compute does not fit.
    """
    if apy_is_int(a) and apy_is_int(b):
        y: i64 = apy_int_payload(b)
        if y == -1:
            return apy_from_int(0)
        if y != 0:
            return apy_from_int(apy_int_payload(a) % y)
    return apy_mod_slow(a, b)


# ── the shifts, which an earlier pass here deliberately skipped ────────────
#
# The note above says they were left to the C "until it is written with both
# guards". The C already had both, correctly, and reading them is what made
# these writable: a negative count is a ValueError, and a count of 64 or more
# is Python's saturating answer rather than whatever `sar` does with
# `count & 63`.


def apy_rshift(a: ptr, b: ptr) -> ptr:
    """`a >> b`, for two plain integers.

    A LONG SHIFT SATURATES rather than being declined, because an arithmetic
    right shift of 64 or more has an exact answer that costs nothing to give:
    `-1 >> 999` is -1 and `5 >> 999` is 0. It is only the MACHINE that has no
    answer there -- x86 shifts by `count & 63`, so reaching the instruction
    would make `1 >> 64` be 1.

    A NEGATIVE COUNT IS DECLINED, not answered: it is a ValueError, and the
    message and the error flag are the C's.
    """
    if apy_is_int(a) and apy_is_int(b):
        n: i64 = apy_int_payload(b)
        if n >= 0:
            x: i64 = apy_int_payload(a)
            if n >= 64:
                if x < 0:
                    return apy_from_int(-1)
                return apy_from_int(0)
            return apy_from_int(x >> n)
    return apy_rshift_slow(a, b)


def apy_lshift(a: ptr, b: ptr) -> ptr:
    """`a << b`, for two plain integers whose result still fits.

    SHIFTED AS UNSIGNED AND CHECKED BY SHIFTING BACK, which is the C's own
    method and is worth keeping for the reason it was chosen: a signed left
    shift that overflows is undefined, so the value is moved through `u64`
    where the behaviour is defined to wrap, and the result is then tested by
    reversing it. If `(r >> n) == x` the shift lost nothing.

    ANYTHING THAT GREW GOES TO THE C, which answers it as a big -- Python has
    no word size, so `1 << 200` is an exact integer and this fast path has no
    way to build one.
    """
    if apy_is_int(a) and apy_is_int(b):
        n: i64 = apy_int_payload(b)
        if n >= 0 and n < 64:
            x: i64 = apy_int_payload(a)
            r: i64 = i64(u64(x) << u64(n))
            if (r >> n) == x:
                return apy_from_int(r)
    return apy_lshift_slow(a, b)
