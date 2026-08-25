# The float and complex cells, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the first port to carry a FLOAT. Every
# ported function before this one moved integers, pointers and bytes; `f64` was
# always a subset type and nothing had needed it.
#
# FOUND THE SAME WAY `runtime/slots.py` WAS -- by asking what a function calls
# that IR does not already define, rather than what `static` helper blocks it.
# Both of these call one thing, `apy_alloc`, which is the C's abort-on-failure
# wrapper around `apy_obj_alloc` -- and `apy_obj_alloc` has been in IR since
# stage 4.
#
# THEY MOVE WHOLE. A constructor that only fills fields in has no case it
# cannot handle, so there is nothing for a slow half to do.
#
# ── on running out of memory ───────────────────────────────────────────────
#
# THE C ABORTS AND THIS PROPAGATES, which is a real difference and worth
# naming. `apy_alloc` prints "out of memory" and calls `exit(1)`; every
# allocation in the ported runtime instead answers null and lets the caller
# answer null, which is what `arena.py` established and what
# `apy_str_copy_bytes` and `apy_obj_alloc` already do.
#
# That convention is the port's, not this file's, and changing it belongs to
# whatever finally decides how a freestanding target reports a failure it
# cannot print.


# THE NUMBERS BELOW ARE THE C COMPILER'S. See `runtime/slots.py` for why they
# are written without docstrings.


def apy_float_kind() -> i64:
    return 3


def apy_complex_kind() -> i64:
    return 18


def apy_float_offset() -> i64:
    return 8


def apy_complex_re_offset() -> i64:
    return 8


def apy_complex_im_offset() -> i64:
    return 16


def apy_from_float(f: f64) -> ptr:
    """A float cell holding `f`.

    THE PAYLOAD IS THE UNION'S FIRST MEMBER, same as an integer's -- a float
    and an int cell differ in their TAG and in how the eight bytes are read,
    not in where those bytes sit. That is the whole of the "type is a field,
    not a mnemonic" rule at the level of one cell.
    """
    o: ptr = apy_obj_alloc(apy_float_kind())
    if not o:
        return o
    store(f64, f, offset(o, apy_float_offset()))
    return o


def apy_from_complex(re: f64, im: f64) -> ptr:
    """A complex cell holding `re + im j`.

    TWO DOUBLES SIDE BY SIDE and no third field: a complex has no separate
    magnitude or flag, so a cell whose real part is zero is what `1j` is.
    """
    o: ptr = apy_obj_alloc(apy_complex_kind())
    if not o:
        return o
    store(f64, re, offset(o, apy_complex_re_offset()))
    store(f64, im, offset(o, apy_complex_im_offset()))
    return o


# -- the float predicates, without libm -------------------------------------
#
# `isnan`, `isinf`, `fabs`, `copysign`, `floor` and `ceil` are all libm names
# and NONE of them is transcendental: every one is a question about the bits
# of an IEEE754 double or a truncation, and both are sayable here. That
# matters because the platform floor is three functions -- a runtime that
# needs `floor` needs a fourth, and it does not have to.
#
# `sqrt`, `log` and the trigonometric family are the real ones and stay in C.


def apy_f64_slot() -> ptr:
    """One word, for reading a double as an integer and back.

    THE ONLY BITCAST THE SUBSET HAS. There is no `reinterpret` operator, but
    `store(f64, ...)` followed by `load(u64, ...)` at the same address is
    exactly one, and it is what every predicate below is built on.

    RESERVED AND NOT `alloca`, because nothing here calls anything: the value
    is stored and read back on the next line.
    """
    return reserve("apy_f64_slot_ir", 8)


def apy_f64_bits(x: f64) -> u64:
    """The IEEE754 bits of `x`, as an integer."""
    slot: ptr = apy_f64_slot()
    store(f64, x, slot)
    return load(u64, slot)


def apy_bits_f64(b: u64) -> f64:
    """A double built from IEEE754 bits."""
    slot: ptr = apy_f64_slot()
    store(u64, b, slot)
    return load(f64, slot)


def apy_isnan_of(x: f64) -> i64:
    """Is `x` a NaN?

    A NaN IS THE ONLY VALUE UNEQUAL TO ITSELF, which is the definition rather
    than a trick, and needs no bits at all.
    """
    if x != x:
        return 1
    return 0


def apy_isinf_of(x: f64) -> i64:
    """Is `x` an infinity?

    `x - x` IS NaN FOR BOTH INFINITIES AND FOR NaN, so the second test is what
    separates them: an infinity equals itself and a NaN does not.
    """
    if x != x:
        return 0
    if x - x != f64(0):
        return 1
    return 0


def apy_fabs_of(x: f64) -> f64:
    """`|x|`.

    THE SIGN BIT IS CLEARED rather than the value negated, which is what makes
    `fabs(-0.0)` answer `+0.0` and `fabs(nan)` answer a NaN without either
    being a special case.
    """
    return apy_bits_f64(apy_f64_bits(x) & u64(0x7FFFFFFFFFFFFFFF))


def apy_copysign_of(x: f64, y: f64) -> f64:
    """`|x|` with `y`'s sign.

    BITS AND NOT A COMPARISON, because `y` may be `-0.0` -- which is less than
    nothing and greater than nothing, and whose sign only the bits carry.
    """
    return apy_bits_f64((apy_f64_bits(x) & u64(0x7FFFFFFFFFFFFFFF))
                        | (apy_f64_bits(y) & u64(0x8000000000000000)))


def apy_trunc_of(x: f64) -> f64:
    """`x` with its fractional part removed, toward zero.

    THROUGH AN INT64 ROUND TRIP, which is exact for every double whose
    magnitude fits -- and a double too big to fit has no fractional part left
    to remove, so it comes back unchanged. NaN and the infinities take the
    same exit for the same reason.
    """
    if x != x:
        return x
    # 2**63 BUILT FROM 2**62, because the literal itself does not fit an
    # int64 -- and `f64(...)` converts FROM one, so writing it directly
    # wrapped to INT64_MIN and made this test true for every value. The
    # symptom was `(1.5).is_integer()` answering True.
    if apy_fabs_of(x) >= f64(4611686018427387904) * f64(2):
        return x
    return f64(i64(x))


def apy_floor_of(x: f64) -> f64:
    """The largest integer not greater than `x`.

    TRUNCATION ROUNDS TOWARD ZERO and floor rounds toward minus infinity, so
    the two differ for exactly the negative non-integers -- which is the one
    adjustment here.
    """
    t: f64 = apy_trunc_of(x)
    if t > x:
        return t - f64(1)
    return t


def apy_ceil_of(x: f64) -> f64:
    """The smallest integer not less than `x`. The mirror of `apy_floor_of`."""
    t: f64 = apy_trunc_of(x)
    if t < x:
        return t + f64(1)
    return t


def apy_math_fabs(v: ptr) -> ptr:
    """`math.fabs(x)` -- always a float, unlike `abs`."""
    x: f64 = apy_math_arg_of(v, rodata(b"fabs\0"))
    if apy_error_occurred():
        return ptr(0)
    return apy_from_float(apy_fabs_of(x))


def apy_math_copysign(a: ptr, b: ptr) -> ptr:
    """`math.copysign(x, y)` -- `|x|` carrying `y`'s sign.

    `-0.0` IS THE WHOLE REASON THIS EXISTS as a function rather than a
    multiplication: it is the one value whose sign a comparison cannot see.
    """
    x: f64 = apy_math_arg_of(a, rodata(b"copysign\0"))
    if apy_error_occurred():
        return ptr(0)
    y: f64 = apy_math_arg_of(b, rodata(b"copysign\0"))
    if apy_error_occurred():
        return ptr(0)
    return apy_from_float(apy_copysign_of(x, y))


def apy_is_integer(v: ptr) -> ptr:
    """`x.is_integer()` -- has this float no fractional part?

    AN INT ALWAYS ANSWERS TRUE, which is Python 3.12 and later: `(5).
    is_integer()` is a real method now, so the int path is not a courtesy.

    AN INFINITY IS NOT AN INTEGER, and the second test is what says so:
    `floor(inf)` is `inf`, so the first test alone would call it one.
    """
    if apy_is_int_like_of(v):
        return apy_from_bool(1)
    if apy_is_big_of(v):
        return apy_from_bool(1)
    if i64(load(i32, offset(v, 0))) != apy_float_kind():
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute "
                   b"'is_integer'%s\0"),
            apy_kind_name_of(v), rodata(b"\0"))
    x: f64 = load(f64, offset(v, apy_float_offset()))
    if x != apy_floor_of(x):
        return apy_from_bool(0)
    if x - x != f64(0):
        return apy_from_bool(0)
    return apy_from_bool(1)


def apy_math_isclose(a: ptr, b: ptr, rel: ptr, abs_tol: ptr) -> ptr:
    """`math.isclose(a, b, rel_tol=, abs_tol=)`.

    THE RELATIVE TOLERANCE SCALES BY THE LARGER MAGNITUDE, which is what makes
    it relative -- and why `isclose(0.0, 1e-9)` is False by default: nothing is
    relatively close to zero, which is what `abs_tol` is for.

    EQUALITY FIRST, so two infinities of the same sign are close: the
    subtraction below would answer NaN for them.

    NaN IS CLOSE TO NOTHING, itself included.
    """
    x: f64 = apy_math_arg_of(a, rodata(b"isclose\0"))
    if apy_error_occurred():
        return ptr(0)
    y: f64 = apy_math_arg_of(b, rodata(b"isclose\0"))
    if apy_error_occurred():
        return ptr(0)
    r: f64 = apy_math_arg_of(rel, rodata(b"isclose\0"))
    if apy_error_occurred():
        return ptr(0)
    t: f64 = apy_math_arg_of(abs_tol, rodata(b"isclose\0"))
    if apy_error_occurred():
        return ptr(0)
    if r < f64(0) or t < f64(0):
        return apy_raise_at(rodata(b"ValueError\0"),
                            rodata(b"tolerances must be non-negative\0"))
    if x == y:
        return apy_from_bool(1)
    if x != x:
        return apy_from_bool(0)
    if y != y:
        return apy_from_bool(0)
    if x - x != f64(0):
        return apy_from_bool(0)
    if y - y != f64(0):
        return apy_from_bool(0)
    d: f64 = apy_fabs_of(x - y)
    ax: f64 = apy_fabs_of(x)
    ay: f64 = apy_fabs_of(y)
    bigger: f64 = ay
    if ax > ay:
        bigger = ax
    if d <= r * bigger:
        return apy_from_bool(1)
    if d <= t:
        return apy_from_bool(1)
    return apy_from_bool(0)
