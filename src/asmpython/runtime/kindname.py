# The kind names, in the machine subset.
#
# WHAT EVERY TypeError SAYS. `apy_kind_name` has sat at the top of
# `asmpython port` since the first survey: a hundred messages name the kind
# they were handed, and none of them could move while this did not.
#
# A SPLIT, AND ONLY ONE CASE MAKES IT ONE. Every kind here answers with a
# literal or with a field read -- except an exception, whose displayed name is
# not the name it matches: a bundled module's classes are spliced under
# mangled names, so `copy.Error` carries `_asmpy_bundled_copy_Error` and
# `apy_exc_shown` maps it back. That mapping is a class lookup, which is
# still C, so an exception goes to the slow half and everything else stays
# here.
#
# THE LITERALS ARE PER-CASE `rodata`, not one packed table with offsets into
# it. A table would be smaller and would need a second thing to be right: the
# offsets. These are compared against nothing, so the shape that cannot drift
# is the one where each name sits at its own use.


# THE NUMBERS BELOW ARE THE C COMPILER'S. See `runtime/slots.py`.


def apy_big_kind() -> i64:
    return 16


def apy_iter_kind() -> i64:
    return 19


def apy_mview_kind() -> i64:
    return 27


def apy_range_kind() -> i64:
    return 28


def apy_super_kind() -> i64:
    return 15


def apy_view_kind() -> i64:
    return 25


def apy_o_dict_offset() -> i64:
    return 16


def apy_o_held_offset() -> i64:
    return 24


def apy_o_cls_offset() -> i64:
    return 8


def apy_t_name_offset() -> i64:
    return 8


def apy_e_name_offset() -> i64:
    return 8


def apy_it_mode_offset() -> i64:
    return 40


def apy_ga_origin_offset() -> i64:
    return 8


def apy_vw_dict_offset() -> i64:
    return 8


def apy_vw_part_offset() -> i64:
    return 16


def apy_fn_bound_offset() -> i64:
    return 48


def apy_big_neg_offset() -> i64:
    return 24


def apy_fn_span() -> i64:
    return 144


def apy_fn_is_type_offset() -> i64:
    return 116


def apy_g_coro_offset2() -> i64:
    return 76


def apy_g_agen_offset2() -> i64:
    return 84


def apy_s_mut_offset() -> i64:
    return 24


def apy_it_map() -> i64:
    return 1


def apy_it_filter() -> i64:
    return 2


def apy_it_enumerate() -> i64:
    return 3


def apy_it_zip() -> i64:
    return 4


def apy_part_keys() -> i64:
    return 0


def apy_part_values() -> i64:
    return 1


def apy_prop_classmethod() -> i64:
    return 1


def apy_prop_staticmethod() -> i64:
    return 2


def apy_kind_name_of(v: ptr) -> ptr:
    """The type name a message would use for `v`, as a C string.

    AN EXCEPTION GOES TO THE SLOW HALF and nothing else does. See the header.

    A BIG IS AN `int`, and that is deliberate rather than an omission. There
    is one integer type in Python and the width is an implementation detail
    this runtime hides -- a program that could tell `2 ** 100` from `5` by its
    type name would be seeing a seam that should not exist.

    AN INSTANCE ANSWERS WITH ITS CLASS'S NAME, which is what makes
    `type(p).__name__` say `Point` and every TypeError about a user object
    name the user's type rather than a word from this file.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_exc_kind():
        return apy_kind_name_of_slow(v)
    if k == apy_none_kind():
        return rodata(b"NoneType\0")
    if k == apy_bool_kind():
        return rodata(b"bool\0")
    if k == apy_int_kind():
        return rodata(b"int\0")
    if k == apy_big_kind():
        return rodata(b"int\0")
    if k == apy_float_kind():
        return rodata(b"float\0")
    if k == apy_complex_kind():
        return rodata(b"complex\0")
    if k == apy_str_kind():
        return rodata(b"str\0")
    if k == apy_bytes_kind():
        # ONE KIND, TWO NAMES: a bytearray is a bytes cell that admits it is
        # mutable, and `mut` is the only thing telling them apart.
        if load(i32, offset(v, apy_s_mut_offset())):
            return rodata(b"bytearray\0")
        return rodata(b"bytes\0")
    if k == apy_list_kind():
        return rodata(b"list\0")
    if k == apy_tuple_kind():
        return rodata(b"tuple\0")
    if k == apy_set_kind():
        return rodata(b"set\0")
    if k == apy_frozen_kind():
        return rodata(b"frozenset\0")
    if k == apy_dict_kind():
        return rodata(b"dict\0")
    if k == apy_inst_kind():
        return apy_str_data(ptr(load(u64, offset(
            ptr(load(u64, offset(v, apy_o_cls_offset()))),
            apy_t_name_offset()))))
    if k == apy_type_kind():
        return rodata(b"type\0")
    if k == apy_func_kind():
        # A CLASS IS A FUNCTION HERE -- `is_type` is what a `class` statement
        # sets, and a program asking `type(C).__name__` must see `type`.
        if load(i32, offset(v, apy_fn_is_type_offset())):
            return rodata(b"type\0")
        if load(i32, offset(v, apy_fn_builtin_offset())):
            return rodata(b"builtin_function_or_method\0")
        return rodata(b"function\0")
    if k == apy_cell_kind():
        return rodata(b"cell\0")
    if k == apy_super_kind():
        return rodata(b"super\0")
    if k == apy_ellipsis_kind():
        return rodata(b"ellipsis\0")
    if k == apy_notimpl_kind():
        return rodata(b"NotImplementedType\0")
    if k == apy_slice_kind():
        return rodata(b"slice\0")
    if k == apy_mview_kind():
        return rodata(b"memoryview\0")
    if k == apy_range_kind():
        return rodata(b"range\0")
    if k == apy_iter_kind():
        # A CURSOR NAMES WHAT MADE IT: `map(str, xs)` is a `map`, which is
        # what `type(...).__name__` answers and what tells a reader why it is
        # lazy. A plain `iter(x)` is an `iterator` -- CPython names those
        # after what they walk (`list_iterator`), which is the one
        # distinction not kept.
        m: i64 = i64(load(i32, offset(v, apy_it_mode_offset())))
        if m == apy_it_map():
            return rodata(b"map\0")
        if m == apy_it_filter():
            return rodata(b"filter\0")
        if m == apy_it_enumerate():
            return rodata(b"enumerate\0")
        if m == apy_it_zip():
            return rodata(b"zip\0")
        return rodata(b"iterator\0")
    if k == apy_view_kind():
        p: i64 = i64(load(i32, offset(v, apy_vw_part_offset())))
        if p == apy_part_keys():
            return rodata(b"dict_keys\0")
        if p == apy_part_values():
            return rodata(b"dict_values\0")
        return rodata(b"dict_items\0")
    if k == apy_prop_kind():
        d: i64 = i64(load(i32, offset(v, apy_prop_kind_offset())))
        if d == apy_prop_classmethod():
            return rodata(b"classmethod\0")
        if d == apy_prop_staticmethod():
            return rodata(b"staticmethod\0")
        return rodata(b"property\0")
    if k == apy_gen_kind():
        # ALL THREE SHARE EVERY FIELD and only the name differs, which a
        # program reads to tell them apart: `async def` with `yield` is an
        # async generator, which is neither of the other two.
        if load(i32, offset(v, apy_g_agen_offset2())):
            return rodata(b"async_generator\0")
        if load(i32, offset(v, apy_g_coro_offset2())):
            return rodata(b"coroutine\0")
        return rodata(b"generator\0")
    if k == apy_alias_kind():
        # A UNION IS NOT A GENERIC ALIAS to a program that asks. `int | str`
        # is built on the `Union` form, and `type(...).__name__` is how a
        # program tells the two apart.
        if i64(load(i32, offset(ptr(load(u64, offset(
                v, apy_ga_origin_offset()))), 0))) == apy_inst_kind():
            return rodata(b"Union\0")
        return rodata(b"types.GenericAlias\0")
    # THE C'S `default` IS `str` AND NOT A REFUSAL, which looks arbitrary and
    # is load-bearing: several cells that never reach a message share the str
    # arm's layout, and answering something is what keeps a stray kind from
    # printing a null pointer.
    return rodata(b"str\0")


# ── two lookups that answer with a pointer ─────────────────────────────────


def apy_str_bytes(s: ptr) -> ptr:
    """The bytes behind a str, bytes or int, for a native call.

    AN INT IS ALLOWED AND MEANS THE ADDRESS ITSELF, because C's own rule for a
    pointer parameter is that a null pointer constant fits it -- and
    `CreateDirectoryA(path, 0)` is the ordinary way to pass "no security
    descriptor". Refusing it would make every native call with a NULL argument
    a TypeError, which is not what the C being declared says.

    THE MESSAGE NAMES THE KIND because `objects_host` words the same refusal
    for the interpreter, and a program that prints the exception would
    otherwise get different text from the two paths -- which the corpus
    compares.
    """
    k: i64 = i64(load(i32, offset(s, 0)))
    if k == apy_int_kind():
        return ptr(apy_int_payload(s))
    if k != apy_str_kind():
        if k != apy_bytes_kind():
            return apy_raise_fmt(
                rodata(b"TypeError\0"),
                rodata(b"a pointer argument must be str, bytes or int, "
                       b"not %s%s\0"),
                apy_kind_name_of(s), rodata(b"\0"))
    return apy_str_data(s)


def apy_type_name(v: ptr) -> ptr:
    """`type(x).__name__`, which the frontend fuses into one call.

    THE CLASS'S OWN NAME VALUE, NOT A FRESH COPY, for an instance: two
    instances of one class must give `type(a).__name__ is type(b).__name__`,
    as they do in CPython, and building a string here would give two.

    `type(C).__name__` IS THE METACLASS'S NAME when one made the class, and
    an ordinary class has no metaclass recorded and is a `type`.

    EVERYTHING ELSE GOES THROUGH THE KIND NAME, which is why this could not
    move until `runtime/kindname.py` did -- and why an exception's answer here
    is the DISPLAYED name rather than the internal one: that split is
    `apy_kind_name_of`'s to make, not this function's.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_inst_kind():
        return ptr(load(u64, offset(
            ptr(load(u64, offset(v, apy_o_cls_offset()))),
            apy_t_name_offset())))
    if k == apy_type_kind():
        meta: ptr = ptr(load(u64, offset(v, apy_t_meta_offset())))
        if meta:
            return ptr(load(u64, offset(meta, apy_t_name_offset())))
        return apy_from_cstr(rodata(b"type\0"))
    return apy_from_cstr(apy_kind_name_of(v))
