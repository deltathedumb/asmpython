# AN INTEGER THAT HAS TO FIT A MACHINE INDEX, and the slice arithmetic above it.
#
# `apy_index_arg` IS A WALL AND NOT A LEAF. Nine call sites reach it and four
# exported functions were waiting on it -- `apy_slice_indices` below,
# `apy_delitem`, and `apy_default_delattr` and `apy_delattr` behind that. It
# is eight lines. That is what the port keeps finding: the thing blocking a
# chain is rarely the big thing in it.
#
# THE OUT-PARAMETER IS A PLAIN WORD, because the C's `int64_t *` is not a type
# the subset can describe -- it has one integer width and `ptr` lowers to
# `uintptr_t`, which gcc rightly calls a conflicting type. The C's delegate
# does the cast, and that is the whole reason the delegate exists.


def apy_sl_start_offset() -> i64:
    return 8


def apy_sl_stop_offset() -> i64:
    return 16


def apy_sl_step_offset() -> i64:
    return 24


def apy_idx_sub() -> i64:
    """`[1, 2][2 ** 100]` -- an IndexError about an index-sized integer."""
    return 0


def apy_idx_repeat() -> i64:
    """`[1, 2] * (2 ** 100)` -- an OverflowError, same wording."""
    return 1


def apy_idx_size() -> i64:
    """`'ab'.ljust(2 ** 100)` -- an OverflowError about a C ssize_t."""
    return 2


def apy_index_arg_of(v: ptr, out: ptr, form: i64) -> i64:
    """An integer argument that has to fit a machine index. 1 on success.

    THERE IS NO ANSWER TO GIVE for a big. Widening this to admit one made
    every payload read behind it a POINTER read as an integer -- silently,
    and with a plausible-looking huge number coming out. A list cannot have
    2**100 elements and a string cannot be padded to 2**100 columns, so
    CPython reports and so does this.

    THREE REPORTS, AND THE PAIRING IS NOT DERIVABLE from anything: it is what
    CPython happens to raise at each of the three places it converts, so it is
    written out rather than reasoned about. See the `apy_idx_*` constants.
    """
    if apy_is_big_of(v):
        kind: ptr = rodata(b"OverflowError\0")
        if form == apy_idx_sub():
            kind = rodata(b"IndexError\0")
        msg: ptr = rodata(b"cannot fit 'int' into an index-sized integer\0")
        if form == apy_idx_size():
            msg = rodata(b"Python int too large to convert to C ssize_t\0")
        apy_raise_at(kind, msg)
        return 0
    store(i64, apy_int_payload(v), out)
    return 1


def apy_slice_slot() -> ptr:
    """Four words, for the bounds `apy_index_arg_of` writes back.

    FOUR AND NOT ONE, because `apy_slice_indices` holds a length, a step, a
    start and a stop at the same time and each is written through a pointer.
    Nothing between the writes and the reads can reach this again.
    """
    return reserve("apy_slice_slot_ir", 32)


def apy_sl_field(sl: ptr, at: i64) -> ptr:
    """One of a slice's three fields, or null when it was not given.

    NOT `apy_slice_bound`, which the C already has with a different
    signature -- the two halves share one translation unit, so a
    subset function wearing a C static's name is `conflicting types`
    from gcc rather than a harmless second copy.

    NULL AND `None` MEAN THE SAME THING HERE, which is what the two tests
    are: an omitted bound is a null field, and a written-out `None` is the
    singleton. `xs[::2]` produces the first and `xs[None::2]` the second, and
    they must slice alike.
    """
    got: ptr = ptr(load(u64, offset(sl, at)))
    if not got:
        return got
    if i64(load(i32, offset(got, 0))) == apy_none_kind():
        return ptr(0)
    return got


def apy_slice_indices(sl: ptr, len_v: ptr) -> ptr:
    """`s.indices(n)` -- the `(start, stop, step)` a walk would really use.

    A PROGRAM USES THIS TO IMPLEMENT `__getitem__` over its own storage
    without reimplementing the clamping rules, which is why it is exported
    rather than being private to the slicer.

    THE DEFAULTS DEPEND ON THE STEP'S SIGN and so do the clamps: walking
    backwards starts at `n - 1` and ends at `-1`, and a start past the end
    clamps to `n - 1` rather than to `n`. Writing one pair of clamps and
    negating afterwards does not work -- `xs[:2]` and `xs[0:2]` differ under
    a negative step, which is what `has_start`/`has_stop` are for.
    """
    if i64(load(i32, offset(sl, 0))) != apy_slice_kind():
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute 'indices'%s\0"),
            apy_kind_name_of(sl), rodata(b"\0"))
    slot: ptr = apy_slice_slot()
    if not apy_index_arg_of(len_v, slot, apy_idx_size()):
        return ptr(0)
    n: i64 = load(i64, slot)
    step: i64 = 1
    have: ptr = apy_sl_field(sl, apy_sl_step_offset())
    if have:
        if not apy_index_arg_of(have, offset(slot, 8), apy_idx_size()):
            return ptr(0)
        step = load(i64, offset(slot, 8))
    if step == 0:
        return apy_raise_at(rodata(b"ValueError\0"),
                            rodata(b"slice step cannot be zero\0"))
    start: i64 = 0
    stop: i64 = n
    if step < 0:
        start = n - 1
        stop = -1
    have = apy_sl_field(sl, apy_sl_start_offset())
    if have:
        if not apy_index_arg_of(have, offset(slot, 16), apy_idx_size()):
            return ptr(0)
        start = load(i64, offset(slot, 16))
        if start < 0:
            start = start + n
        if start < 0:
            start = 0
            if step < 0:
                start = -1
        if start > n:
            start = n
            if step < 0:
                start = n - 1
    have = apy_sl_field(sl, apy_sl_stop_offset())
    if have:
        if not apy_index_arg_of(have, offset(slot, 24), apy_idx_size()):
            return ptr(0)
        stop = load(i64, offset(slot, 24))
        if stop < 0:
            stop = stop + n
        if stop < 0:
            stop = 0
            if step < 0:
                stop = -1
        if stop > n:
            stop = n
            if step < 0:
                stop = n - 1
    out: ptr = apy_tuple_new(3)
    if not out:
        return out
    apy_seq_push(out, apy_from_int(start))
    apy_seq_push(out, apy_from_int(stop))
    apy_seq_push(out, apy_from_int(step))
    return out
