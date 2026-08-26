# MAKING a big integer, where the rest of the port could only READ one.
#
# IR had `apy_mag_cmp_of`, `apy_mag_bits_of`, `apy_big_popcount` and the base
# conversions -- every one of them a reader. Nothing here could build a big,
# which is why `apy_math_floor`, `apy_math_ceil`, `apy_as_integer_ratio` and
# the whole float-to-integer boundary were still C: they all end in "and now
# make a big out of that".
#
# `apy_obj *` CROSSES AS A PLAIN WORD, the convention `apy_mag_cmp_of` set:
# the subset has no pointer-to-struct to declare, so these take and answer
# `ptr` and the C's delegates cast.
#
# A LIMB IS 32 BITS. Reading pairs of them as one 64-bit word gives the right
# answer on a little-endian machine for most values and reads twice as far as
# the array holds. `apy_limb_size` is what the C's `apy_limb` is, and every
# index here goes through it.
#
# THE ARENA REPLACES TWO THINGS AT ONCE. `apy_big_alloc` calls `calloc` and
# then `fputs`/`exit` if it fails -- so both of the libc names the survey
# reported as blockers for this were the same allocation. `apy_alloc_bytes`
# answers zero instead, and every caller here checks it.


def apy_big_alloc_of(n: i64) -> ptr:
    """A big object with `n` zeroed limbs. Zero if there is no room.

    AT LEAST ONE LIMB even when asked for none, because a zero magnitude is
    still an array -- `apy_mag_trim_of` sets `n` to 0 and leaves the storage
    alone, and a null limb pointer would fault the next reader.
    """
    room: i64 = n
    if room < 1:
        room = 1
    o: ptr = apy_obj_alloc(apy_big_kind())
    if not o:
        return o
    limb: ptr = apy_alloc_bytes(room * apy_limb_size())
    if not limb:
        return limb
    i: i64 = 0
    while i < room:
        store(u32, u32(0), offset(limb, i * apy_limb_size()))
        i = i + 1
    store(u64, u64(limb), offset(o, apy_big_limb_offset()))
    store(i64, n, offset(o, apy_big_n_offset()))
    store(i32, i32(0), offset(o, apy_big_neg_offset()))
    return o


def apy_mag_trim_of(o: ptr) -> ptr:
    """Drop leading zero limbs. The object, so this can be used inline."""
    n: i64 = load(i64, offset(o, apy_big_n_offset()))
    limb: ptr = ptr(load(u64, offset(o, apy_big_limb_offset())))
    while n > 0:
        if load(u32, offset(limb, (n - 1) * apy_limb_size())) != u32(0):
            return apy_big_set_n(o, n)
        n = n - 1
    return apy_big_set_n(o, 0)


def apy_big_set_n(o: ptr, n: i64) -> ptr:
    """Write a magnitude's limb count back and answer the object."""
    store(i64, n, offset(o, apy_big_n_offset()))
    return o


def apy_big_done_of(o: ptr) -> ptr:
    """Trim, then DEMOTE if the value fits an int64.

    THE INVARIANT OF THE WHOLE BIG SECTION, enforced in the one place every
    result passes through: a value that fits a machine integer is never a
    big. Two bigs that hold the same number would otherwise compare unequal
    to the int that also holds it.

    THE BOUND DIFFERS BY ONE BETWEEN THE SIGNS because -2**63 is
    representable and +2**63 is not, and the negation goes through unsigned
    because negating INT64_MIN is undefined.
    """
    apy_mag_trim_of(o)
    n: i64 = load(i64, offset(o, apy_big_n_offset()))
    if n == 0:
        return apy_from_int(0)
    if n <= 2:
        limb: ptr = ptr(load(u64, offset(o, apy_big_limb_offset())))
        m: u64 = u64(load(u32, limb))
        if n == 2:
            m = m | (u64(load(u32, offset(limb, apy_limb_size()))) << 32)
        if load(i32, offset(o, apy_big_neg_offset())):
            if m <= u64(9223372036854775807) + u64(1):
                return apy_from_int(i64(u64(0) - m))
        elif m <= u64(9223372036854775807):
            return apy_from_int(i64(m))
    return o


def apy_big_of_i64_of(v: i64) -> ptr:
    """An int64 as a magnitude plus a sign. NEVER NORMALISED.

    It is an operand and not a result -- feeding a mixed operation into the
    big path -- so it keeps its two limbs and its sign rather than demoting
    straight back to the integer it came from.
    """
    o: ptr = apy_big_alloc_of(2)
    if not o:
        return o
    m: u64 = u64(v)
    if v < 0:
        m = u64(0) - m
    limb: ptr = ptr(load(u64, offset(o, apy_big_limb_offset())))
    store(u32, u32(m & u64(4294967295)), limb)
    store(u32, u32(m >> 32), offset(limb, apy_limb_size()))
    if v < 0:
        store(i32, i32(1), offset(o, apy_big_neg_offset()))
    if load(u32, offset(limb, apy_limb_size())) == u32(0):
        if load(u32, limb) == u32(0):
            return apy_big_set_n(o, 0)
        return apy_big_set_n(o, 1)
    return o


def apy_as_big_of(v: ptr) -> ptr:
    """Either integer kind as a big object.

    A BOOL ARRIVES HERE TOO -- `True` is 1 for arithmetic -- which is why
    this reads the payload rather than checking for the int kind alone.
    """
    if i64(load(i32, offset(v, 0))) == apy_big_kind():
        return v
    return apy_big_of_i64_of(apy_int_payload(v))


def apy_mag_shl_of(a: ptr, bits: i64) -> ptr:
    """A magnitude shifted left by `bits`. A new object.

    A SHIFT BY A WHOLE LIMB IS UNDEFINED IN C and would be here too: when
    `off` is zero the second store shifts by 32, which is the same trap
    `apy_intop` documents for `<< 64`. Skipping it is correct as well as
    safe -- there is nothing to carry.
    """
    words: i64 = bits // apy_limb_bits()
    off: i64 = bits % apy_limb_bits()
    n: i64 = load(i64, offset(a, apy_big_n_offset()))
    if n == 0:
        return apy_big_alloc_of(0)
    r: ptr = apy_big_alloc_of(n + words + 1)
    if not r:
        return r
    src: ptr = ptr(load(u64, offset(a, apy_big_limb_offset())))
    dst: ptr = ptr(load(u64, offset(r, apy_big_limb_offset())))
    i: i64 = 0
    while i < n:
        t: u64 = u64(load(u32, offset(src, i * apy_limb_size()))) << u64(off)
        at: ptr = offset(dst, (i + words) * apy_limb_size())
        store(u32, load(u32, at) | u32(t & u64(4294967295)), at)
        if off:
            up: ptr = offset(dst, (i + words + 1) * apy_limb_size())
            store(u32, load(u32, up) | u32(t >> 32), up)
        i = i + 1
    return apy_mag_trim_of(r)


def apy_big_from_double_of(f: f64) -> ptr:
    """A double of magnitude at least 2**63, and therefore an exact integer.

    NOTHING HERE CAN LOSE A BIT, which is the whole point: a double that big
    has no fractional part left, so its mantissa IS the integer and the
    exponent says where to put it. The C reaches for `frexp` and `ldexp` to
    take it apart; those are pure IEEE754 bit reads and the subset can do
    them directly.

    THE IMPLICIT LEADING 1 IS PUT BACK. A normalised double stores 52
    mantissa bits and means 53, and a value of magnitude 2**63 or more is
    always normalised -- so there is no subnormal case to worry about and the
    shift below is always to the LEFT.
    """
    b: u64 = apy_f64_bits(apy_fabs_of(f))
    mant: i64 = i64((b & u64(4503599627370495)) | u64(4503599627370496))
    e: i64 = i64(b >> 52) - 1023 - 52
    o: ptr = apy_big_of_i64_of(mant)
    if not o:
        return o
    if e > 0:
        o = apy_mag_shl_of(o, e)
        if not o:
            return o
    if f < f64(0):
        store(i32, i32(1), offset(o, apy_big_neg_offset()))
    return apy_big_done_of(o)


def apy_whole_of(d: f64) -> ptr:
    """A double that is already a whole number, as an integer value.

    THE BOUND IS 2**63 AND IT IS BUILT FROM 2**62, because the literal does
    not fit an int64 and `f64(...)` converts FROM one -- writing it directly
    wrapped to INT64_MIN and made the test true for every value.
    """
    limit: f64 = f64(4611686018427387904) * f64(2)
    if d >= limit:
        return apy_big_from_double_of(d)
    if d < f64(0) - limit:
        return apy_big_from_double_of(d)
    return apy_from_int(i64(d))


def apy_whole_int(v: ptr) -> ptr:
    """An integer that is already whole, answered as `math` would answer it.

    A BOOL BECOMES AN INT. `math.floor(True)` is `1` in CPython and not
    `True` -- these functions answer an integer, and a bool is one only by
    inheritance. Handing the argument straight back was the obvious thing and
    it printed `True`.
    """
    if i64(load(i32, offset(v, 0))) == apy_bool_kind():
        return apy_from_int(apy_int_payload(v))
    return v


def apy_math_floor(v: ptr) -> ptr:
    """`math.floor(x)`. An integer already there is answered unchanged."""
    if apy_is_int_like_of(v):
        return apy_whole_int(v)
    if apy_is_big_of(v):
        return v
    x: f64 = apy_math_arg_of(v, rodata(b"floor\0"))
    if apy_err_kind():
        return ptr(0)
    return apy_whole_of(apy_floor_of(x))


def apy_math_ceil(v: ptr) -> ptr:
    """`math.ceil(x)`."""
    if apy_is_int_like_of(v):
        return apy_whole_int(v)
    if apy_is_big_of(v):
        return v
    x: f64 = apy_math_arg_of(v, rodata(b"ceil\0"))
    if apy_err_kind():
        return ptr(0)
    return apy_whole_of(apy_ceil_of(x))


def apy_math_trunc(v: ptr) -> ptr:
    """`math.trunc(x)` -- toward zero, which is neither floor nor ceil."""
    if apy_is_int_like_of(v):
        return apy_whole_int(v)
    if apy_is_big_of(v):
        return v
    x: f64 = apy_math_arg_of(v, rodata(b"trunc\0"))
    if apy_err_kind():
        return ptr(0)
    if x < f64(0):
        return apy_whole_of(apy_ceil_of(x))
    return apy_whole_of(apy_floor_of(x))
