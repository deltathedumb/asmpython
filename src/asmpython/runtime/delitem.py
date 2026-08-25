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


def apy_default_delattr(obj: ptr, name: ptr) -> ptr:
    """`del obj.name` -- `object.__delattr__`, after the hooks have declined.

    ONE MESSAGE FOR TWO FAILURES, deliberately: an object with no instance
    dict at all and an object whose dict does not hold the name are the same
    thing from the program's side, and CPython reports them alike.

    THE LOOKUP IS SEPARATE FROM THE DELETE because `apy_delitem` reports a
    KeyError and this owes an AttributeError. Letting the delete raise its own
    message would name a dict the program never wrote.
    """
    if i64(load(i32, offset(obj, 0))) != apy_inst_kind():
        return apy_no_such_attr(obj, name)
    d: ptr = ptr(load(u64, offset(obj, apy_o_dict_offset())))
    if apy_dict_find_of(d, name) < 0:
        return apy_no_such_attr(obj, name)
    return apy_delitem(d, name)


def apy_no_such_attr(obj: ptr, name: ptr) -> ptr:
    """`'C' object has no attribute 'x'`.

    THE NAME'S BYTES AND NOT ITS CELL: `apy_raise_fmt` copies both arguments
    with `apy_cstr_into`, so it wants C strings.
    """
    return apy_raise_fmt(
        rodata(b"AttributeError\0"),
        rodata(b"'%s' object has no attribute '%s'\0"),
        apy_kind_name_of(obj),
        ptr(load(u64, offset(name, apy_str_ptr_offset()))))


def apy_delattr(obj: ptr, name: ptr) -> ptr:
    """`del obj.name`, with the hooks that may take it first.

    THREE CHANCES BEFORE THE INSTANCE DICT, in CPython's order:

      `__delattr__` on the class takes EVERY delete, whatever the name --
      the same rule `__setattr__` has, and the reason it is asked before
      anything is looked up at all.

      A DATA DESCRIPTOR TAKES THE DELETE exactly as it takes the write.
      `__delete__` is the third of the three, and a property or a user
      descriptor defining it never reaches the instance dict. Without this,
      `del c.d` on a descriptor attribute looked in the dict, found nothing
      -- a descriptor never puts anything there -- and reported an attribute
      the class plainly has.

      A PROPERTY WITH NO DELETER REFUSES rather than falling through, for
      the same reason: falling through would report the attribute missing.

    ANYTHING ELSE IS `object.__delattr__`.
    """
    if i64(load(i32, offset(obj, 0))) == apy_inst_kind():
        cls: ptr = ptr(load(u64, offset(obj, apy_o_cls_offset())))
        hook: ptr = apy_class_find_of(cls, apy_name_of(rodata(b"__delattr__\0")))
        if hook:
            argv: ptr = alloca(8)
            store(u64, u64(name), argv)
            return apy_call(apy_bind_of(hook, obj), argv, 1)
        found: ptr = apy_class_find_of(cls, name)
        if found:
            if apy_is_data_descriptor_of(found):
                one: ptr = alloca(8)
                store(u64, u64(obj), one)
                if i64(load(i32, offset(found, 0))) == apy_prop_kind():
                    dele: ptr = ptr(load(
                        u64, offset(found, apy_prop_del_offset())))
                    if not dele:
                        return apy_raise_at(
                            rodata(b"AttributeError\0"),
                            rodata(b"can't delete attribute\0"))
                    return apy_call(dele, one, 1)
                m: ptr = apy_class_find_of(
                    ptr(load(u64, offset(found, apy_o_cls_offset()))),
                    apy_name_of(rodata(b"__delete__\0")))
                if m:
                    return apy_call(apy_bind_of(m, found), one, 1)
    return apy_default_delattr(obj, name)
