# The cursor cell, in the machine subset.
#
# WHAT `iter(x)`, `map`, `filter`, `enumerate` AND `zip` ALL ARE. One cell with
# a mode, so the five differ in a number rather than in a layout.


# THE NUMBERS BELOW ARE THE C COMPILER'S. See `runtime/slots.py`.


def apy_it_src_offset() -> i64:
    return 8


def apy_it_fn_offset() -> i64:
    return 16


def apy_it_i_offset() -> i64:
    return 24


def apy_it_n0_offset() -> i64:
    return 32


def apy_str_cmp_of(a: ptr, b: ptr) -> i64:
    """-1, 0 or 1 for two strings or two bytes, compared as bytes.

    THE SHORTER LENGTH FIRST, then the lengths themselves: `memcmp` over the
    common prefix decides it unless the prefix is equal, and then the shorter
    string is the smaller one. `'ab' < 'abc'` falls out of that rather than
    being tested for.

    A SIGN, NOT A DIFFERENCE. `memcmp` may answer any negative or positive
    number, and callers compare against -1 and 1 -- so the sign is narrowed
    here rather than at each of them.

    BY BYTE, WHICH IS BY CODE POINT for UTF-8: the encoding was designed so
    that byte order and code point order agree, which is the one place in
    this runtime where comparing bytes is not an approximation of comparing
    characters.
    """
    an: i64 = apy_str_byte_len(a)
    bn: i64 = apy_str_byte_len(b)
    n: i64 = an
    if bn < n:
        n = bn
    p: ptr = apy_str_data(a)
    q: ptr = apy_str_data(b)
    i: i64 = 0
    while i < n:
        x: i64 = i64(load(u8, offset(p, i)))
        y: i64 = i64(load(u8, offset(q, i)))
        if x < y:
            return -1
        if x > y:
            return 1
        i = i + 1
    if an == bn:
        return 0
    if an < bn:
        return -1
    return 1


def apy_cursor_of(src: ptr, fn: ptr, mode: i64, start: i64) -> ptr:
    """A cursor over `src`, in `mode`, positioned at `start`.

    ONE CELL FOR FIVE THINGS. `iter(x)`, `map`, `filter`, `enumerate` and
    `zip` differ in the mode and in whether `fn` is set, not in their layout
    -- which is what makes `apy_step` one function rather than five.

    `n0` REMEMBERS A DICT'S SIZE and is -1 for everything else. That is the
    RuntimeError machinery: a dict that changes length while it is being
    walked has to be caught, and comparing against the size at the start is
    how. A list may grow while iterated -- Python allows it -- so nothing
    else records one.
    """
    o: ptr = apy_obj_alloc(apy_iter_kind())
    if not o:
        return o
    store(u64, u64(src), offset(o, apy_it_src_offset()))
    store(u64, u64(fn), offset(o, apy_it_fn_offset()))
    store(i32, i32(mode), offset(o, apy_it_mode_offset()))
    store(i64, start, offset(o, apy_it_i_offset()))
    n0: i64 = -1
    if src:
        if i64(load(i32, offset(src, 0))) == apy_dict_kind():
            n0 = load(i64, offset(src, apy_d_n_offset()))
    store(i64, n0, offset(o, apy_it_n0_offset()))
    return o


# ── equality, the fast half ────────────────────────────────────────────────
#
# `apy_eq_raw` IS WHAT EVERYTHING LEFT BOTTOMS OUT ON: dict lookup, set
# membership, `in`, `==`, and through those the whole set family. It is also
# the largest function in the runtime that is not `apy_text` -- ranges,
# instances with `__eq__`, memoryviews, views-as-sets, sets, dicts,
# sequences, complexes, slices, bound methods and the numeric tower, each
# with its own rule.
#
# SO IT SPLITS RATHER THAN MOVES. What is here is the part a program spends
# its time in -- two integers, two strings, two of anything compared by
# identity -- and everything else goes back to the C, which still has the
# whole switch.
#
# THE FAST PATH ONLY ANSWERS WHEN BOTH SIDES ARE THE SAME KIND. Mixed pairs
# are where the interesting rules live (`1 == 1.0`, `True == 1`, a str
# against bytes) and every one of them is a decision this half declines to
# make.


def apy_eq_raw_of(a: ptr, b: ptr) -> i64:
    """Are `a` and `b` equal? The IR half of a split.

    IDENTITY IS NOT ENOUGH FOR A FLOAT, which is the one trap here: a cell
    compared with itself is equal for every kind except a float holding NaN,
    and `x != x` is the whole definition of NaN. So the identity shortcut is
    taken only after the kind has been checked.

    A BIG IS DECLINED even though its kind matches: comparing two needs
    `apy_big_cmp`, and the big integers are still C.

    A BOOL AGAINST AN int IS DECLINED too, though `True == 1` is true and the
    payloads would say so. The rule is `both the same kind`, and widening it
    to `both int-like` would mean this half deciding something the C's
    numeric tower decides -- for no gain a program would notice, because the
    slow half gets it right.
    """
    ka: i64 = i64(load(i32, offset(a, 0)))
    if ka != i64(load(i32, offset(b, 0))):
        return apy_eq_raw_of_slow(a, b)
    if ka == apy_int_kind():
        if apy_int_payload(a) == apy_int_payload(b):
            return 1
        return 0
    if ka == apy_bool_kind():
        if apy_int_payload(a) == apy_int_payload(b):
            return 1
        return 0
    if ka == apy_none_kind():
        return 1
    if ka == apy_str_kind():
        if apy_str_cmp_of(a, b) == 0:
            return 1
        return 0
    if ka == apy_bytes_kind():
        if apy_str_cmp_of(a, b) == 0:
            return 1
        return 0
    return apy_eq_raw_of_slow(a, b)


# The cursor mode that just walks its source, rather than mapping, filtering
# or zipping as it goes.
def apy_it_plain() -> i64:
    return 0


def apy_enumerate(seq: ptr, start: i64) -> ptr:
    """`enumerate(seq, start)`.

    THE COUNT LIVES IN THE CURSOR\'S OWN SLOT, which is what `start` fills:
    there is nothing to allocate and nothing to hold between steps beyond a
    number the cursor was already keeping.
    """
    src: ptr = apy_getiter(seq)
    if not src:
        return ptr(0)
    return apy_cursor_of(src, ptr(0), apy_it_enumerate(), start)


def apy_filter(fn: ptr, seq: ptr) -> ptr:
    """`filter(fn, seq)`.

    LAZY, AND THAT MATTERS: `filter` over an infinite generator is a normal
    thing to write, so the predicate is applied while stepping rather than
    once up front.
    """
    src: ptr = apy_getiter(seq)
    if not src:
        return ptr(0)
    return apy_cursor_of(src, fn, apy_it_filter(), 0)


def apy_map(fn: ptr, seq: ptr) -> ptr:
    """`map(fn, seq)` -- lazy, for the reason `apy_filter` gives."""
    src: ptr = apy_getiter(seq)
    if not src:
        return ptr(0)
    return apy_cursor_of(src, fn, apy_it_map(), 0)


def apy_zip_n(buf: ptr, argc: i64, strict: i64) -> ptr:
    """`zip(a, b, ...)`, and `strict=` beside it.

    THE CURSORS ARE MADE UP FRONT AND THE STEPPING IS NOT: every argument is
    turned into an iterator here, because `zip(5, [1])` is an error about the
    5 and has to be one before anything is produced. What each iterator
    YIELDS is still asked for one round at a time.

    `strict` RIDES IN THE FUNCTION SLOT rather than in a mode of its own,
    which is why it is boxed: the cursor has one spare value and this is what
    zip needs it for.
    """
    cursors: ptr = apy_seq_new_of(apy_list_kind(), argc + 1)
    if not cursors:
        return cursors
    k: i64 = 0
    while k < argc:
        got: ptr = apy_getiter(ptr(load(u64, offset(
            buf, k * apy_value_size()))))
        if not got:
            return ptr(0)
        apy_seq_push(cursors, got)
        k = k + 1
    flag: i64 = 0
    if strict != 0:
        flag = 1
    return apy_cursor_of(cursors, apy_from_bool(flag), apy_it_zip(), 0)


def apy_zip2(a: ptr, b: ptr) -> ptr:
    """`zip(a, b)` -- the two-argument spelling, which is nearly all of them.

    THE ARRAY IS BUILT HERE rather than by the caller, because the frontend
    emits this shape for a plain two-sequence zip and would otherwise have to
    reserve a stack slot for a call it makes constantly.
    """
    pair: ptr = alloca(16)
    store(u64, u64(a), pair)
    store(u64, u64(b), offset(pair, apy_value_size()))
    return apy_zip_n(pair, 2, 0)


# -- stepping an iterator, which is what every `for` in a program reaches ---


def apy_not_an_iterator_at(it: ptr) -> ptr:
    """`next(x)` where `x` walks nothing."""
    return apy_raise_fmt(
        rodata(b"TypeError\0"),
        rodata(b"'%s' object is not an iterator%s\0"),
        apy_kind_name_of(it), rodata(b"\0"))


def apy_step(it: ptr) -> ptr:
    """One element from `it`, or `apy_stop()` when there are no more.

    THE SENTINEL IS A VALUE AND NOT AN ERROR, which is what makes a `for`
    loop a test rather than a handler: `apy_stop()` is one shared object and
    the caller compares against it. A null is a real failure and propagates.

    A USER ITERATOR ENDS BY RAISING, because that is the protocol -- so
    StopIteration is caught here and turned into the sentinel, and anything
    else raised inside `__next__` is a failure the loop must not swallow.

    THE FIVE CURSOR MODES ARE ONE FUNCTION because they share the source
    walk: map, filter, enumerate and zip each step something else and shape
    what comes back, and only the plain mode reads a container directly.

    `filter(None, xs)` KEEPS THE TRUTHY ONES, a real form, and why the
    callable is TESTED rather than simply called.

    THE LENGTH IS READ EVERY STEP in the plain mode, which is the point: a
    body that appends to the list it is walking sees the new elements and one
    that shortens it stops early -- both as CPython does. A DICT that changed
    size is refused instead, because the table is rehashed by the write and
    continuing would skip or repeat entries.
    """
    k: i64 = i64(load(i32, offset(it, 0)))
    if k == apy_gen_kind():
        slot: ptr = alloca(8)
        v: ptr = apy_gen_step_of(it, apy_none(), slot)
        if not v:
            return ptr(0)
        if load(i64, slot):
            return apy_stop()
        return v
    if k == apy_inst_kind():
        got: ptr = apy_unary_dunder_of(it, rodata(b"__next__\0"))
        if got:
            return got
        if apy_error_matches(apy_from_cstr(rodata(b"StopIteration\0"))):
            apy_error_clear()
            return apy_stop()
        if apy_error_occurred():
            return ptr(0)
        return apy_not_an_iterator_at(it)
    if k != apy_iter_kind():
        return apy_not_an_iterator_at(it)
    mode: i64 = i64(load(i32, offset(it, apy_it_mode_offset())))
    src: ptr = ptr(load(u64, offset(it, apy_it_src_offset())))
    fn: ptr = ptr(load(u64, offset(it, apy_it_fn_offset())))
    if mode == apy_it_map():
        v: ptr = apy_step(src)
        if not v:
            return v
        if v == apy_stop():
            return v
        one: ptr = alloca(8)
        store(u64, u64(v), one)
        return apy_call(fn, one, 1)
    if mode == apy_it_filter():
        going: i64 = 1
        while going:
            v: ptr = apy_step(src)
            if not v:
                return v
            if v == apy_stop():
                return v
            keep: ptr = v
            if i64(load(i32, offset(fn, 0))) != apy_none_kind():
                one: ptr = alloca(8)
                store(u64, u64(v), one)
                keep = apy_call(fn, one, 1)
            if not keep:
                return ptr(0)
            if apy_truth(keep):
                return v
        return apy_stop()
    if mode == apy_it_enumerate():
        v: ptr = apy_step(src)
        if not v:
            return v
        if v == apy_stop():
            return v
        pair: ptr = apy_seq_new_of(apy_tuple_kind(), 2)
        if not pair:
            return pair
        at: i64 = load(i64, offset(it, apy_it_i_offset()))
        store(i64, at + 1, offset(it, apy_it_i_offset()))
        apy_seq_push(pair, apy_from_int(at))
        apy_seq_push(pair, v)
        return pair
    if mode == apy_it_zip():
        n: i64 = load(i64, offset(src, apy_q_n_offset()))
        if n == 0:
            return apy_stop()
        row: ptr = apy_seq_new_of(apy_tuple_kind(), n + 1)
        if not row:
            return row
        items: ptr = ptr(load(u64, offset(src, apy_q_items_offset())))
        i: i64 = 0
        while i < n:
            v: ptr = apy_step(ptr(load(u64, offset(
                items, i * apy_value_size()))))
            if not v:
                return ptr(0)
            if v == apy_stop():
                if fn:
                    if apy_truth(fn):
                        if i > 0:
                            return apy_raise_at(
                                rodata(b"ValueError\0"),
                                rodata(b"zip() argument 2 is shorter than "
                                       b"argument 1\0"))
                return apy_stop()
            apy_seq_push(row, v)
            i = i + 1
        return row
    at: i64 = load(i64, offset(it, apy_it_i_offset()))
    if i64(load(i32, offset(src, 0))) == apy_inst_kind():
        got: ptr = apy_getitem(src, apy_from_int(at))
        if not got:
            if apy_error_matches(apy_from_cstr(rodata(b"IndexError\0"))):
                apy_error_clear()
                return apy_stop()
            return ptr(0)
        store(i64, at + 1, offset(it, apy_it_i_offset()))
        return got
    n: i64 = apy_raw_len(src)
    if apy_error_occurred():
        return ptr(0)
    n0: i64 = load(i64, offset(it, apy_it_n0_offset()))
    if n0 >= 0:
        if i64(load(i32, offset(src, 0))) == apy_dict_kind():
            if n != n0:
                return apy_raise_at(
                    rodata(b"RuntimeError\0"),
                    rodata(b"dictionary changed size during "
                           b"iteration\0"))
    if at >= n:
        return apy_stop()
    store(i64, at + 1, offset(it, apy_it_i_offset()))
    return apy_key_at(src, at)


def apy_walk_getitem(v: ptr) -> ptr:
    """Walk `v[0]`, `v[1]`, ... until it reports IndexError.

    THE OLD ITERATION PROTOCOL, and the guard is what makes it safe to run at
    all: a `__getitem__` that never raises would otherwise not stop. A million
    is the C\'s number and is kept.
    """
    out: ptr = apy_seq_new_of(apy_list_kind(), 8)
    if not out:
        return out
    guard: i64 = 0
    going: i64 = 1
    while going:
        if guard >= 1000000:
            going = 0
        else:
            got: ptr = apy_getitem(v, apy_from_int(guard))
            if not got:
                if apy_error_matches(
                        apy_from_cstr(rodata(b"IndexError\0"))):
                    apy_error_clear()
                    going = 0
                else:
                    return ptr(0)
            else:
                apy_seq_push(out, got)
                guard = guard + 1
    return out


def apy_walk_next(it: ptr) -> ptr:
    """Call `__next__` until it raises StopIteration, keeping what it gave."""
    out: ptr = apy_seq_new_of(apy_list_kind(), 8)
    if not out:
        return out
    guard: i64 = 0
    going: i64 = 1
    while going:
        if guard >= 1000000:
            going = 0
        else:
            got: ptr = apy_unary_dunder_of(it, rodata(b"__next__\0"))
            if not got:
                if apy_error_matches(
                        apy_from_cstr(rodata(b"StopIteration\0"))):
                    apy_error_clear()
                    going = 0
                else:
                    return ptr(0)
            else:
                apy_seq_push(out, got)
                guard = guard + 1
    return out


def apy_has_next_method(v: ptr) -> i64:
    """Is `v` an instance whose class defines `__next__`?"""
    if i64(load(i32, offset(v, 0))) != apy_inst_kind():
        return 0
    if apy_class_find_of(ptr(load(u64, offset(v, apy_o_cls_offset()))),
                         apy_name_of(rodata(b"__next__\0"))):
        return 1
    return 0


def apy_iterable(v: ptr) -> ptr:
    """`v` as something with elements that can be READ MORE THAN ONCE.

    NOT `apy_getiter`, and the difference is the whole point: that one
    answers a cursor, which is consumed by walking it. This answers a
    CONTAINER, because its callers -- `len`, `sorted`, unpacking, `in` --
    each need to look at the elements and then look again.

    ASKING A LAZY THING FOR ITS ELEMENTS RUNS IT, and the honest answer is to
    run it once and keep the result. What is consumed stays consumed.

    A CLASS WITH `__len__` AND NO `__iter__` IS ANSWERED AS ITSELF, because
    the caller was asking a length question and the object can answer it
    directly -- draining it through `__getitem__` first would be work whose
    result is thrown away.

    A HELD BUILTIN IS UNWRAPPED only when the class did not override the
    walk: `class C(list)` with no `__iter__` of its own IS its list, and one
    with an `__iter__` means the body it wrote.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_view_kind():
        return apy_view_items(v)
    if k == apy_gen_kind():
        return apy_gen_drain(v)
    if k == apy_type_kind():
        meta: ptr = ptr(load(u64, offset(v, apy_t_meta_offset())))
        if meta:
            hook: ptr = apy_class_find_of(
                meta, apy_name_of(rodata(b"__iter__\0")))
            if hook:
                got: ptr = apy_call(apy_bind_of(hook, v), ptr(0), 0)
                if not got:
                    return ptr(0)
                return apy_iterable(got)
    if k != apy_inst_kind():
        return v
    cls: ptr = ptr(load(u64, offset(v, apy_o_cls_offset())))
    held: ptr = ptr(load(u64, offset(v, apy_o_held_offset())))
    if held:
        if not apy_class_find_of(cls, apy_name_of(rodata(b"__iter__\0"))):
            return apy_iterable(held)
    it: ptr = apy_unary_dunder_of(v, rodata(b"__iter__\0"))
    if apy_error_occurred():
        return ptr(0)
    if not it:
        if apy_unary_dunder_of(v, rodata(b"__len__\0")):
            return v
        if apy_error_occurred():
            return ptr(0)
        if not apy_class_find_of(cls,
                                 apy_name_of(rodata(b"__getitem__\0"))):
            return apy_raise_fmt(
                rodata(b"TypeError\0"),
                rodata(b"'%s' object is not iterable%s\0"),
                apy_kind_name_of(v), rodata(b"\0"))
        return apy_walk_getitem(v)
    ik: i64 = i64(load(i32, offset(it, 0)))
    if ik != apy_gen_kind() and ik != apy_iter_kind():
        if not apy_is_seq_of(it) and not apy_is_set_of(it):
            if ik != apy_dict_kind():
                if not apy_has_next_method(it):
                    return apy_raise_fmt(
                        rodata(b"TypeError\0"),
                        rodata(b"iter() returned non-iterator of "
                               b"type '%s'%s\0"),
                        apy_kind_name_of(it), rodata(b"\0"))
    if not apy_has_next_method(it):
        return apy_iterable(it)
    return apy_walk_next(it)


def apy_iter(v: ptr) -> ptr:
    """`iter(v)` as a PROGRAM spells it, which is not quite `apy_getiter`.

    THE DIFFERENCE IS WHAT AN INSTANCE ANSWERS. `apy_getiter` is the runtime
    asking for something to walk and will wrap an object with only
    `__getitem__`; this is the builtin, and a class that answers its own
    iterator gets to -- `iter(x) is x.__iter__()` for a class that wrote one.

    A CURSOR IS ALREADY ONE, so `iter(it) is it` holds, which every partly
    consumed iterator relies on.

    A VIEW WALKS WHAT IT IS A VIEW OF, which `apy_getiter` and
    `apy_iterable` both already did and this one did not:
    `iter(d.items())` refused a thing `list(d.items())` accepts.

    THE DICT SIZE IS LATCHED at the moment the cursor is made, which is what
    lets `apy_step` refuse a dict that changed size under the walk. -1 for
    anything else means "no size to check".
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_iter_kind():
        return v
    if k == apy_view_kind():
        return apy_iter(apy_view_items(v))
    if k == apy_type_kind():
        meta: ptr = ptr(load(u64, offset(v, apy_t_meta_offset())))
        if meta:
            hook: ptr = apy_class_find_of(
                meta, apy_name_of(rodata(b"__iter__\0")))
            if hook:
                return apy_call(apy_bind_of(hook, v), ptr(0), 0)
    if k == apy_gen_kind():
        return v
    if k == apy_inst_kind():
        got: ptr = apy_unary_dunder_of(v, rodata(b"__iter__\0"))
        if got:
            return got
        if apy_error_occurred():
            return ptr(0)
        held: ptr = ptr(load(u64, offset(v, apy_o_held_offset())))
        if held:
            return apy_iter(held)
        made: ptr = apy_iterable(v)
        if not made:
            return ptr(0)
        if made != v:
            return apy_iter(made)
    if not apy_is_seq_of(v) and not apy_is_set_of(v):
        if k != apy_str_kind() and k != apy_bytes_kind():
            if k != apy_dict_kind() and k != apy_range_kind():
                return apy_raise_fmt(
                    rodata(b"TypeError\0"),
                    rodata(b"'%s' object is not iterable%s\0"),
                    apy_kind_name_of(v), rodata(b"\0"))
    o: ptr = apy_obj_alloc(apy_iter_kind())
    if not o:
        return o
    store(u64, u64(v), offset(o, apy_it_src_offset()))
    store(i32, i32(apy_it_plain()), offset(o, apy_it_mode_offset()))
    latched: i64 = -1
    if k == apy_dict_kind():
        latched = load(i64, offset(v, apy_d_n_offset()))
    store(i64, latched, offset(o, apy_it_n0_offset()))
    return o


def apy_reversed(seq: ptr) -> ptr:
    """`reversed(seq)`.

    A CLASS MAY SAY WHAT ITS REVERSE IS, and `__reversed__` is asked first --
    which is the only way `reversed` can mean anything for an object that is
    not indexable.

    A SET IS REFUSED, because it has no order to reverse: Python calls it not
    reversible rather than answering an arbitrary order.

    EAGER, NOT LAZY: a list is built rather than a cursor walked backwards,
    which is what makes the result readable more than once.
    """
    if i64(load(i32, offset(seq, 0))) == apy_inst_kind():
        hook: ptr = apy_unary_dunder_of(seq, rodata(b"__reversed__\0"))
        if apy_error_occurred():
            return ptr(0)
        if hook:
            return apy_iterable(hook)
    if apy_is_set_of(seq):
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object is not reversible%s\0"),
            apy_kind_name_of(seq), rodata(b"\0"))
    n: i64 = apy_raw_len(seq)
    if apy_error_occurred():
        return ptr(0)
    out: ptr = apy_seq_new_of(apy_list_kind(), n + 1)
    if not out:
        return out
    i: i64 = n - 1
    while i >= 0:
        apy_seq_push(out, apy_key_at(seq, i))
        i = i - 1
    return out


def apy_extend(seq: ptr, other: ptr) -> ptr:
    """`xs.extend(other)`, and the `*rest` of a list display.

    THROUGH `apy_iterable` FIRST, so a generator is drained and a view is
    unwrapped -- `xs.extend(g())` is ordinary and would otherwise have no
    elements to read.

    THE INDEXED KINDS ARE WALKED BY INDEX and the sequence kinds by their
    items array, which is the same split every container walk here makes: a
    str has no items array and a list has no `__getitem__` worth going
    through.
    """
    src: ptr = apy_iterable(other)
    if not src:
        return ptr(0)
    k: i64 = i64(load(i32, offset(src, 0)))
    if (k == apy_str_kind() or k == apy_bytes_kind()
            or k == apy_dict_kind() or k == apy_range_kind()):
        n: i64 = apy_raw_len(src)
        i: i64 = 0
        while i < n:
            item: ptr = ptr(0)
            if k == apy_dict_kind():
                keys: ptr = ptr(load(u64, offset(src, apy_d_keys_offset())))
                item = ptr(load(u64, offset(keys, i * apy_value_size())))
            else:
                item = apy_getitem(src, apy_from_int(i))
            if not item:
                return ptr(0)
            apy_seq_push(seq, item)
            i = i + 1
        return apy_none()
    if not apy_is_seq_of(src) and not apy_is_set_of(src):
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object is not iterable%s\0"),
            apy_kind_name_of(src), rodata(b"\0"))
    qn: i64 = load(i64, offset(src, apy_q_n_offset()))
    items: ptr = ptr(load(u64, offset(src, apy_q_items_offset())))
    j: i64 = 0
    while j < qn:
        apy_seq_push(seq, ptr(load(u64, offset(
            items, j * apy_value_size()))))
        j = j + 1
    return apy_none()


def apy_delegate_step(src: ptr, sent: ptr) -> ptr:
    """One step of a `yield from`, carrying whatever was sent in.

    A GENERATOR TAKES THE SENT VALUE and anything else cannot, which is the
    whole reason this is not just `apy_step`: `g.send(x)` through a
    `yield from` has to reach the inner generator.
    """
    if i64(load(i32, offset(src, 0))) == apy_gen_kind():
        slot: ptr = alloca(8)
        v: ptr = apy_gen_step_of(src, sent, slot)
        if not v:
            return ptr(0)
        if load(i64, slot):
            return apy_stop()
        return v
    return apy_step(src)


def apy_excgroup_new(msg: ptr, excs: ptr) -> ptr:
    """`ExceptionGroup(msg, excs)`.

    AN EMPTY GROUP IS REFUSED, which is PEP 654: a group with nothing in it
    would be caught by an `except*` that matches nothing, and there is no
    useful answer to what it contains.
    """
    src: ptr = apy_iterable(excs)
    if not src:
        return ptr(0)
    if not apy_is_seq_of(src):
        return apy_raise_at(
            rodata(b"TypeError\0"),
            rodata(b"second argument (exceptions) must be a sequence\0"))
    if load(i64, offset(src, apy_q_n_offset())) == 0:
        return apy_raise_at(
            rodata(b"ValueError\0"),
            rodata(b"second argument (exceptions) must be a non-empty "
                   b"sequence\0"))
    g: ptr = apy_make_exc(apy_from_cstr(rodata(b"ExceptionGroup\0")), msg)
    if not g:
        return ptr(0)
    store(u64, u64(src), offset(g, apy_e_subs_offset()))
    return g


def apy_every_of(v: ptr, want: i64, otherwise: i64) -> ptr:
    """`all(v)` and `any(v)`, which are one walk with two answers.

    THE FIRST ELEMENT THAT DECIDES ENDS IT, which is what makes both short-
    circuit: `all` stops at the first falsey and `any` at the first truthy,
    and `want` is which one is being looked for.

    AN EMPTY SEQUENCE IS `otherwise` -- True for `all` and False for `any`,
    which is Python\'s rule and falls out of reaching the end without having
    decided.

    LAZY, THROUGH A CURSOR: `any(f(x) for x in xs)` must not evaluate `f`
    past the element that answered.
    """
    it: ptr = apy_getiter(v)
    if not it:
        return ptr(0)
    going: i64 = 1
    while going:
        item: ptr = apy_step(it)
        if not item:
            return ptr(0)
        if item == apy_stop():
            return apy_from_bool(otherwise)
        if apy_truth(item) == want:
            return apy_from_bool(want)
    return apy_from_bool(otherwise)


def apy_all(v: ptr) -> ptr:
    """`all(v)`."""
    return apy_every_of(v, 0, 1)


def apy_any(v: ptr) -> ptr:
    """`any(v)`."""
    return apy_every_of(v, 1, 0)


def apy_iter_until(fn: ptr, sentinel: ptr) -> ptr:
    """`iter(f, sentinel)` -- the CALLABLE form.

    NOTHING LAZY UNDERNEATH, so the calls all happen here and the result is a
    cursor over what they returned. A generator would be the honest shape and
    there is none to build one from; the guard is what keeps a callable that
    never answers the sentinel from running forever.

    THE SENTINEL IS COMPARED BY VALUE, not by identity: `iter(f, "")` stops
    on any empty string, which is what CPython does.
    """
    out: ptr = apy_seq_new_of(apy_list_kind(), 8)
    if not out:
        return out
    guard: i64 = 0
    going: i64 = 1
    while going:
        if guard >= 1000000:
            going = 0
        else:
            v: ptr = apy_call(fn, ptr(0), 0)
            if not v:
                return ptr(0)
            if apy_truth(apy_eq(v, sentinel)):
                going = 0
            else:
                apy_seq_push(out, v)
                guard = guard + 1
    return apy_iter(out)


def apy_next(it: ptr, fallback: ptr, has_default: i64) -> ptr:
    """`next(it)`, and `next(it, default)`.

    A GENERATOR ENDS THROUGH `apy_gen_stop` and anything else through a bare
    StopIteration, which is the difference that matters: only a generator has
    a return value to carry out, and only in the exception can it travel.
    """
    k: i64 = i64(load(i32, offset(it, 0)))
    if k != apy_gen_kind() and k != apy_iter_kind() and k != apy_inst_kind():
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object is not an iterator%s\0"),
            apy_kind_name_of(it), rodata(b"\0"))
    got: ptr = apy_step(it)
    if not got:
        return ptr(0)
    if got == apy_stop():
        if has_default:
            return fallback
        if k == apy_gen_kind():
            return apy_gen_stop(it)
        return apy_raise_at(rodata(b"StopIteration\0"), rodata(b"\0"))
    return got
