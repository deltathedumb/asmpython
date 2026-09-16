# WHAT AN EXCEPTION LOOKS LIKE. `ValueError('bad')`, and `bad` under `str()`.
#
# THE ASYMMETRY IS PYTHON'S: `str(e)` is the MESSAGE and `repr(e)` is the call
# that would rebuild it. Both are here because they are one walk over the same
# three fields, and separating them would mean asking twice which of the two
# strange cases applies.
#
# THE TWO STRANGE CASES, both of which have cost real bugs:
#
#   MORE THAN ONE ARGUMENT PRINTS AS THE TUPLE. `str(ValueError('a', 'b'))` is
#   `('a', 'b')` and its repr is `ValueError('a', 'b')` -- CPython shows the
#   whole of `args` once there is more than one, and rendering only the first
#   silently dropped the rest.
#
#   KeyError SHOWS THE REPR OF ITS KEY, alone among exceptions: `str(KeyError('k'))`
#   is `"'k'"` and not `k`, so that a missing key whose text is empty, or is
#   itself a message, is still visible in the report. And a KeyError REBUILT
#   from a failed lookup already holds the repr -- that is what `rendered`
#   records -- so quoting it again gave `KeyError("'k'")` where CPython says
#   `KeyError('k')`.
#
# WHICH IS WHY THE NAME IS COMPARED AS TEXT. The C reaches for `strcmp` and
# the subset has no libc, so the comparison is written out; it is nine bytes
# against a literal and runs once per repr of an exception.


def apy_e_argv_offset() -> i64:
    return 32


def apy_cstr_len(p: ptr) -> i64:
    """`strlen`, written out. The one thing the C had here that IR does not."""
    n: i64 = 0
    while load(u8, offset(p, n)) != 0:
        n = n + 1
    return n


def apy_exc_is_key(v: ptr) -> i64:
    """Is this the one exception that shows its argument quoted?"""
    if apy_cstr_eq(ptr(load(u64, offset(v, apy_e_name_offset()))),
                   rodata(b"KeyError\0")):
        return 1
    return 0


def apy_exc_under_os(v: ptr) -> i64:
    """Is this exception anywhere under `OSError`?

    NOT THE C STATIC'S NAME, and deliberately: the C and the IR
    are one translation unit, so a subset function wearing a C
    static's name is `conflicting types` from gcc rather than a
    harmless second copy. This replaces nothing, so it is named
    something else.

    A WALK AND NOT A NAME TEST, because the whole family arrives here under
    its own name: `open` on a missing file raises FileNotFoundError, and
    `errno` maps a dozen others onto their own classes. Asking only whether
    the name IS `OSError` would have answered no for every one that a real
    program actually catches.
    """
    at: ptr = ptr(load(u64, offset(v, apy_e_name_offset())))
    while at:
        if apy_cstr_eq(at, rodata(b"OSError\0")):
            return 1
        at = apy_exc_parent_of(at)
    return 0


def apy_exc_wrap(v: ptr, shown: ptr, bare: i64) -> ptr:
    """`Name(shown)`, or `Nameshown` when the text brings its own brackets.

    `bare` IS FOR THE TUPLE CASE, where the tuple's own parentheses ARE the
    call's -- so the text is spliced in whole rather than wrapped again and
    `ValueError('a', 'b')` does not come out as `ValueError(('a', 'b'))`.
    """
    name: ptr = apy_exc_shown_of(
        ptr(load(u64, offset(v, apy_e_name_offset()))))
    head: i64 = apy_cstr_len(name)
    m: i64 = load(i64, offset(shown, apy_str_len_offset()))
    buf: ptr = apy_alloc_bytes(head + m + 3)
    if not buf:
        return buf
    out: i64 = apy_cstr_into(buf, 0, head, name)
    if not bare:
        store(u8, u8(40), offset(buf, out))
        out = out + 1
    out = apy_text_into(buf, out, shown)
    if not bare:
        store(u8, u8(41), offset(buf, out))
        out = out + 1
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)


def apy_errno_text(v: ptr, argv: ptr) -> ptr:
    """`[Errno 2] No such file`, and `: 'f.txt'` when a filename came too.

    ZERO WHEN THIS IS NOT THAT SHAPE, so the caller falls through to the
    ordinary rendering. TWO OR THREE ARGUMENTS ONLY: `OSError('plain')` is an
    ordinary exception with an ordinary message, and `OSError()` has nothing
    to say at all -- CPython puts the errno form on exactly this shape and
    leaves every other one alone.

    THE FILENAME IS QUOTED AND THE MESSAGE IS NOT, which looks inconsistent
    and is deliberate on CPython's part: the message is prose meant to be
    read, and the filename is a value a reader may need to see the exact
    bytes of -- a trailing space in a path is invisible otherwise.
    """
    n: i64 = load(i64, offset(argv, apy_q_n_offset()))
    if n < 2:
        return ptr(0)
    if n > 3:
        return ptr(0)
    items: ptr = ptr(load(u64, offset(argv, apy_q_items_offset())))
    a0: ptr = ptr(load(u64, items))
    if i64(load(i32, offset(a0, 0))) != apy_int_kind():
        return ptr(0)
    num: ptr = apy_decimal_of(apy_int_payload(a0), 0)
    msg: ptr = apy_text_of(ptr(load(u64, offset(items, apy_value_size()))), 0)
    if not msg:
        return msg
    tail: ptr = ptr(0)
    if n == 3:
        tail = apy_text_of(
            ptr(load(u64, offset(items, 2 * apy_value_size()))), 1)
        if not tail:
            return tail
    room: i64 = apy_cstr_len(num) + load(
        i64, offset(msg, apy_str_len_offset())) + 10
    if tail:
        room = room + load(i64, offset(tail, apy_str_len_offset())) + 2
    buf: ptr = apy_alloc_bytes(room)
    if not buf:
        return buf
    out: i64 = apy_cstr_into(buf, 0, room, rodata(b"[Errno \0"))
    out = apy_cstr_into(buf, out, room, num)
    store(u8, u8(93), offset(buf, out))
    store(u8, u8(32), offset(buf, out + 1))
    out = apy_text_into(buf, out + 2, msg)
    if tail:
        store(u8, u8(58), offset(buf, out))
        store(u8, u8(32), offset(buf, out + 1))
        out = apy_text_into(buf, out + 2, tail)
    store(u8, u8(0), offset(buf, out))
    return apy_from_bytes(buf, out)


def apy_exc_text_of(v: ptr, quoted: i64) -> ptr:
    """`str(e)` when `quoted` is 0, `repr(e)` when it is 1."""
    argv: ptr = ptr(load(u64, offset(v, apy_e_argv_offset())))
    if argv:
        if apy_is_seq_of(argv):
            if not quoted:
                if apy_exc_under_os(v):
                    errno: ptr = apy_errno_text(v, argv)
                    if errno:
                        return errno
            if load(i64, offset(argv, apy_q_n_offset())) > 1:
                shown: ptr = apy_text_of(argv, 1)
                if not shown:
                    return shown
                if not quoted:
                    return shown
                return apy_exc_wrap(v, shown, 1)
    # WHETHER THERE WAS AN ARGUMENT, not whether it is None. `str(E())` is
    # empty and `str(E(None))` is `None`; `repr` shows `E()` and `E(None)`.
    # Testing the argument's KIND conflated the two, so an exception
    # deliberately carrying None lost it.
    has: i64 = i64(load(i32, offset(v, apy_e_has_arg_offset())))
    arg: ptr = ptr(load(u64, offset(v, apy_e_arg_offset())))
    rendered: i64 = i64(load(i32, offset(v, apy_e_rendered_offset())))
    key: i64 = apy_exc_is_key(v)
    if not quoted:
        if not has:
            return apy_from_cstr(rodata(b"\0"))
        # QUOTED WHEN IT IS A KeyError THAT HAS NOT ALREADY BEEN QUOTED.
        if key:
            if not rendered:
                return apy_text_of(arg, 1)
        return apy_text_of(arg, 0)
    twice: i64 = 0
    if key:
        if rendered:
            twice = 1
    if not has:
        return apy_exc_wrap(v, apy_from_cstr(rodata(b"\0")), 0)
    inner: ptr = apy_text_of(arg, 1 - twice)
    if not inner:
        return inner
    return apy_exc_wrap(v, inner, 0)
