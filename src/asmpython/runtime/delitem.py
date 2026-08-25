# `del d[k]`, `del xs[i]`, `del xs[1:3]`, and `del obj.name`.
#
# TWO CONTAINERS AND FOUR FAILURE MODES, and CPython's own wording for each.
# The shapes look alike and are not: a dict deletes by KEY and reports the key
# it could not find, a list deletes by INDEX and reports a range, and a slice
# deletes a SPAN and is not an index at all.
#
# THE SLICE CASE IS CHECKED FIRST FOR THAT LAST REASON. Falling through to the
# index path asked `apy_index_arg` for an integer, got a slice, and reported
# an IndexError about a subscript the program never wrote.
#
# INSERTION ORDER IS PRESERVED BY SHIFTING, not by swapping the last entry
# into the hole. Dict order has been part of the language since 3.7, so the
# swap would be a WRONG ANSWER rather than a faster one -- and the list has
# never been allowed to reorder on delete at all.


def apy_delitem(seq: ptr, key: ptr) -> ptr:
    """`del seq[key]`. None on success, zero with an error set on failure."""
    if i64(load(i32, offset(seq, 0))) == apy_inst_kind():
        # `del obj[k]` IS `obj.__delitem__(k)`. Never dispatched before, so a
        # class that wrote one had it ignored and the delete was reported as
        # unsupported -- a wrong answer about the class's own method.
        r: ptr = apy_method1_of(seq, rodata(b"__delitem__\0"), key)
        if r:
            return r
        if apy_err_kind():
            return ptr(0)
        # A CLASS THAT EXTENDS A BUILTIN deletes from the one it carries.
        held: ptr = apy_inst_held_of(seq)
        if held:
            return apy_delitem(held, key)
    if i64(load(i32, offset(seq, 0))) == apy_dict_kind():
        bad: ptr = apy_unhashable_of(key)
        if bad:
            return apy_raise_fmt(rodata(b"TypeError\0"),
                                 rodata(b"unhashable type: '%s'%s\0"),
                                 bad, rodata(b"\0"))
        i: i64 = apy_dict_find_of(seq, key)
        if i < 0:
            shown: ptr = apy_repr(key)
            if not shown:
                return shown
            # THE BYTES AND NOT THE CELL. `apy_raise_fmt` copies its two
            # arguments with `apy_cstr_into`, so both are C strings -- handing
            # it a str cell copied from the header, whose first byte is the
            # kind tag's low byte, and the KeyError came out empty.
            return apy_raise_fmt(
                rodata(b"KeyError\0"), rodata(b"%s%s\0"),
                ptr(load(u64, offset(shown, apy_str_ptr_offset()))),
                rodata(b"\0"))
        keys: ptr = ptr(load(u64, offset(seq, apy_d_keys_offset())))
        vals: ptr = ptr(load(u64, offset(seq, apy_d_vals_offset())))
        n: i64 = load(i64, offset(seq, apy_d_n_offset()))
        while i + 1 < n:
            store(u64, load(u64, offset(keys, (i + 1) * apy_value_size())),
                  offset(keys, i * apy_value_size()))
            store(u64, load(u64, offset(vals, (i + 1) * apy_value_size())),
                  offset(vals, i * apy_value_size()))
            i = i + 1
        store(i64, n - 1, offset(seq, apy_d_n_offset()))
        return apy_none()
    if i64(load(i32, offset(seq, 0))) != apy_list_kind():
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object doesn't support item deletion%s\0"),
            apy_kind_name_of(seq), rodata(b"\0"))
    items: ptr = ptr(load(u64, offset(seq, apy_q_items_offset())))
    have: i64 = load(i64, offset(seq, apy_q_n_offset()))
    if key:
        if i64(load(i32, offset(key, 0))) == apy_slice_kind():
            return apy_del_span(seq, key)
    slot: ptr = apy_delitem_slot()
    if not apy_index_arg_of(key, slot, apy_idx_sub()):
        return ptr(0)
    at: i64 = load(i64, slot)
    if at < 0:
        at = at + have
    if at < 0 or at >= have:
        return apy_raise_at(rodata(b"IndexError\0"),
                            rodata(b"list assignment index out of range\0"))
    while at + 1 < have:
        store(u64, load(u64, offset(items, (at + 1) * apy_value_size())),
              offset(items, at * apy_value_size()))
        at = at + 1
    store(i64, have - 1, offset(seq, apy_q_n_offset()))
    return apy_none()


def apy_delitem_slot() -> ptr:
    """One word, for the index `apy_index_arg_of` writes back."""
    return reserve("apy_delitem_slot_ir", 8)


def apy_del_span(seq: ptr, key: ptr) -> ptr:
    """`del xs[1:3]` -- the slice case, which removes a SPAN.

    STEP 1 ONLY, which is a real limit and not an oversight: CPython deletes
    a strided slice too, and doing it needs a second pass that compacts around
    the survivors rather than one that shifts the tail down. Refusing is the
    honest answer until that is written.
    """
    n: i64 = load(i64, offset(seq, apy_q_n_offset()))
    bounds: ptr = apy_slice_indices(key, apy_from_int(n))
    if not bounds:
        return bounds
    b: ptr = ptr(load(u64, offset(bounds, apy_q_items_offset())))
    start: i64 = apy_int_payload(ptr(load(u64, b)))
    stop: i64 = apy_int_payload(ptr(load(u64, offset(b, apy_value_size()))))
    step: i64 = apy_int_payload(
        ptr(load(u64, offset(b, 2 * apy_value_size()))))
    if step != 1:
        return apy_raise_at(
            rodata(b"ValueError\0"),
            rodata(b"only step 1 slice deletion is supported\0"))
    if start < 0:
        start = 0
    if stop > n:
        stop = n
    if stop < start:
        stop = start
    items: ptr = ptr(load(u64, offset(seq, apy_q_items_offset())))
    frm: i64 = stop
    to: i64 = start
    while frm < n:
        store(u64, load(u64, offset(items, frm * apy_value_size())),
              offset(items, to * apy_value_size()))
        frm = frm + 1
        to = to + 1
    store(i64, n - (stop - start), offset(seq, apy_q_n_offset()))
    return apy_none()
