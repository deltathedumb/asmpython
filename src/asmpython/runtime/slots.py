# The slice and the descriptor, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the first port outside the string and
# number work. What made it reachable is not a new capability -- it is a
# better question, and the question is CLOSURE.
#
# ── the metric that matters ────────────────────────────────────────────────
#
# The count that guided this port for a long time was "how many exported
# functions are blocked by a `static` C helper", and it said 89% and pointed
# at `apy_fail2` and `apy_kind_name`. That is a real obstacle but it is the
# WRONG MEASURE, because the invariant the inert runtime actually keeps is
# stronger: the ported runtime CALLS NOTHING IT DOES NOT DEFINE. That is what
# lets a backend owe three functions and no more.
#
# Under the right measure most of the "unblocked" functions turn out to be
# unportable after all -- `apy_hasattr` uses no static at all, but it calls
# `apy_getattr`, so porting it would open a hole in the closure. And a few
# functions nobody had noticed turn out to be ready, which is how these two
# were found: every callee they have is already in IR.
#
# ── what these two are ─────────────────────────────────────────────────────
#
# CELL CONSTRUCTION AND NOTHING ELSE. Both ask the arena for a cell, fill in
# three or four fields and hand it back; there is no walk, no allocation of
# bytes, no decision. That is why they move WHOLE rather than splitting: there
# is no case they cannot handle, so there is nothing for a slow half to do.
#
# `apy_obj_alloc` ZEROES THE PAYLOAD, which both of these rely on and only one
# of them says so in the C. `apy_descr_new` writes `set` and `del_` as 0
# explicitly; `apy_slice_new` writes all three of its fields, so it never had
# to care. The zeroing is kept in view here because a fourth field added to
# either arm would otherwise be uninitialised in exactly one of them.


# THE NUMBERS BELOW ARE THE C COMPILER'S, not anyone's reading of the enum:
# `tests/asmpython/integration/test_ported_int.py` asks a real compiler for
# each one and fails if a line here disagrees. They are written without
# docstrings because that probe reads them as one-line constants.


def apy_slice_kind() -> i64:
    return 23


def apy_prop_kind() -> i64:
    return 26


def apy_slice_start_offset() -> i64:
    return 8


def apy_slice_stop_offset() -> i64:
    return 16


def apy_slice_step_offset() -> i64:
    return 24


def apy_prop_get_offset() -> i64:
    return 8


def apy_prop_set_offset() -> i64:
    return 16


def apy_prop_del_offset() -> i64:
    return 24


def apy_prop_kind_offset() -> i64:
    return 32


def apy_slice_new(start: ptr, stop: ptr, step: ptr) -> ptr:
    """`slice(start, stop, step)`.

    THE THREE FIELDS ARE VALUES, NOT NUMBERS. A slice holds whatever it was
    given -- None for an omitted bound is the common case, and `a[::2]` puts
    None in two of the three. Nothing here interprets them; that is the job of
    whoever subscripts with the slice.
    """
    o: ptr = apy_obj_alloc(apy_slice_kind())
    if not o:
        return o
    store(u64, u64(start), offset(o, apy_slice_start_offset()))
    store(u64, u64(stop), offset(o, apy_slice_stop_offset()))
    store(u64, u64(step), offset(o, apy_slice_step_offset()))
    return o


def apy_descr_new(fn: ptr, kind: i64) -> ptr:
    """A descriptor over `fn` -- a property, a classmethod, a staticmethod.

    ONLY THE GETTER IS SET. `property(f)` has no setter and no deleter until
    `@x.setter` builds a new descriptor, so both are written as zero rather
    than left to the allocator -- which does zero them, and which the C also
    does not trust for exactly this pair.

    `kind` IS THE ONE NUMBER, and it is what tells a `classmethod` from a
    `staticmethod` from a `property` at lookup time. It is stored as a
    narrower field than the rest, which is why it gets its own store width.
    """
    o: ptr = apy_obj_alloc(apy_prop_kind())
    if not o:
        return o
    store(u64, u64(fn), offset(o, apy_prop_get_offset()))
    store(u64, u64(0), offset(o, apy_prop_set_offset()))
    store(u64, u64(0), offset(o, apy_prop_del_offset()))
    store(i32, i32(kind), offset(o, apy_prop_kind_offset()))
    return o


# ── the property decorators, and one list method ───────────────────────────
#
# THE FIRST THINGS THE FIXED SURVEY FOUND. `asmpython port` was reading a
# fraction of the C -- its comment-and-literal stripper deleted most of the
# runtime -- and these four had never once appeared on its list.
#
# `PROPERTY` IS ZERO, which is why `apy_descr_new` needs no kind argument
# here: it is the first member of the enum and every one of these builds a
# property rather than a classmethod or a staticmethod.


def apy_prop_kind_property() -> i64:
    """The descriptor kind a `@property` makes. First of its enum."""
    return 0


def apy_prop_new_from(prop: ptr, get: ptr, set_: ptr, del_: ptr) -> ptr:
    """A fresh property carrying three functions.

    A NEW DESCRIPTOR EVERY TIME, NOT A MUTATION. `@x.setter` reads as though
    it changes `x`, and it must not: the class body binds the RESULT, and a
    property shared by two classes would otherwise gain a setter in both.
    That is also why each of the three below passes the two halves it is not
    replacing rather than leaving them for the allocator.
    """
    out: ptr = apy_descr_new(get, apy_prop_kind_property())
    if not out:
        return out
    store(u64, u64(set_), offset(out, apy_prop_set_offset()))
    store(u64, u64(del_), offset(out, apy_prop_del_offset()))
    return out


def apy_prop_part(prop: ptr, which: i64) -> ptr:
    """One of a property's three functions, by offset."""
    return ptr(load(u64, offset(prop, which)))


def apy_prop_refuse(prop: ptr, name: ptr) -> ptr:
    """The AttributeError all three share, worded for the one that asked."""
    return apy_raise_fmt(
        rodata(b"AttributeError\0"),
        rodata(b"'%s' object has no attribute '%s'\0"),
        apy_kind_name_of(prop), name)


def apy_prop_getter(prop: ptr, fn: ptr) -> ptr:
    """`@x.getter` -- the same property with a new reader."""
    if i64(load(i32, offset(prop, 0))) != apy_prop_kind():
        return apy_prop_refuse(prop, rodata(b"getter\0"))
    return apy_prop_new_from(prop, fn,
                             apy_prop_part(prop, apy_prop_set_offset()),
                             apy_prop_part(prop, apy_prop_del_offset()))


def apy_prop_setter(prop: ptr, fn: ptr) -> ptr:
    """`@x.setter` -- the same property with a new writer."""
    if i64(load(i32, offset(prop, 0))) != apy_prop_kind():
        return apy_prop_refuse(prop, rodata(b"setter\0"))
    return apy_prop_new_from(prop,
                             apy_prop_part(prop, apy_prop_get_offset()),
                             fn,
                             apy_prop_part(prop, apy_prop_del_offset()))


def apy_prop_deleter(prop: ptr, fn: ptr) -> ptr:
    """`@x.deleter` -- the same property with a new remover."""
    if i64(load(i32, offset(prop, 0))) != apy_prop_kind():
        return apy_prop_refuse(prop, rodata(b"deleter\0"))
    return apy_prop_new_from(prop,
                             apy_prop_part(prop, apy_prop_get_offset()),
                             apy_prop_part(prop, apy_prop_set_offset()),
                             fn)


def apy_list_reverse(seq: ptr) -> ptr:
    """`xs.reverse()` -- in place, and only for a list.

    A TUPLE IS REFUSED BY THE SAME CHECK THAT ADMITS A LIST, which is the
    point of testing the kind rather than testing for `v.q`: both arms have
    items and a count, and reversing a tuple in place would erase the one
    distinction between them.

    HALF THE WALK, because each swap places two elements. A loop to `n`
    would reverse the list and then reverse it back.
    """
    if i64(load(i32, offset(seq, 0))) != apy_list_kind():
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute 'reverse'%s\0"),
            apy_kind_name_of(seq), rodata(b"\0"))
    n: i64 = load(i64, offset(seq, apy_q_n_offset()))
    items: ptr = ptr(load(u64, offset(seq, apy_q_items_offset())))
    i: i64 = 0
    while i < n // 2:
        lo: ptr = offset(items, i * apy_value_size())
        hi: ptr = offset(items, (n - 1 - i) * apy_value_size())
        held: u64 = load(u64, lo)
        store(u64, load(u64, hi), lo)
        store(u64, held, hi)
        i = i + 1
    return apy_none()


def apy_fn_dict_offset() -> i64:
    return 144


# -- setting an attribute, which is six exported functions deep --------------


def apy_is_data_descriptor_of(v: ptr) -> i64:
    """Does `v` want to intercept a WRITE as well as a read?

    THE DISTINCTION DECIDES WHO WINS. A data descriptor beats the instance
    dict; a non-data one (a plain method, a `classmethod`) loses to it, which
    is what lets `c.m = 5` shadow a method.

    A `classmethod` OR `staticmethod` IS NOT ONE, which is why the kind is
    checked rather than the tag alone -- all three share a cell.
    """
    k: i64 = i64(load(i32, offset(v, 0)))
    if k == apy_prop_kind():
        if i64(load(i32, offset(v, apy_prop_kind_offset()))) == \
                apy_prop_kind_property():
            return 1
        return 0
    if k != apy_inst_kind():
        return 0
    cls: ptr = ptr(load(u64, offset(v, apy_o_cls_offset())))
    if apy_class_find_of(cls, apy_name_of(rodata(b"__set__\0"))):
        return 1
    if apy_class_find_of(cls, apy_name_of(rodata(b"__delete__\0"))):
        return 1
    return 0


def apy_descr_set_of(d: ptr, obj: ptr, value: ptr) -> i64:
    """Hand a write to a data descriptor. -1 if it is not one.

    THREE ANSWERS AND THE CALLER NEEDS ALL OF THEM: -1 means "not mine, store
    it yourself", 1 means "taken", 0 means "taken and it failed". Folding the
    last two together would make a setter that raised look like a name the
    instance dict should have stored.

    A `property` WITH NO SETTER IS AN ERROR AND NOT A FALLTHROUGH: `c.v = 4`
    on a read-only property must refuse rather than quietly shadow it.
    """
    if not apy_is_data_descriptor_of(d):
        return -1
    if i64(load(i32, offset(d, 0))) == apy_prop_kind():
        setter: ptr = ptr(load(u64, offset(d, apy_prop_set_offset())))
        if not setter:
            apy_raise_at(rodata(b"AttributeError\0"),
                         rodata(b"can't set attribute\0"))
            return 1
        argv: ptr = alloca(16)
        store(u64, u64(obj), argv)
        store(u64, u64(value), offset(argv, apy_value_size()))
        if apy_call(setter, argv, 2):
            return 1
        return 0
    m: ptr = apy_class_find_of(
        ptr(load(u64, offset(d, apy_o_cls_offset()))),
        apy_name_of(rodata(b"__set__\0")))
    if not m:
        return -1
    argv2: ptr = alloca(16)
    store(u64, u64(obj), argv2)
    store(u64, u64(value), offset(argv2, apy_value_size()))
    if apy_call(apy_bind_of(m, d), argv2, 2):
        return 1
    return 0


def apy_slot_allows_of(cls: ptr, name: ptr) -> i64:
    """May `name` be stored on an instance of `cls`?

    TWO WALKS, AND THE FIRST IS THE POINT: a class anywhere in the chain
    WITHOUT `__slots__` gives every instance a dict, so any name is allowed
    and the second walk never runs. Only when every class declares one does
    the name have to appear in one of them.

    A BARE STRING IS ONE SLOT. `__slots__ = "x"` is legal and means the same
    as `("x",)`, which is why the string case is tested before the walk --
    iterating it would allow "x" and also allow nothing else of length one.

    AN UNREADABLE `__slots__` ALLOWS EVERYTHING rather than refusing: this is
    a permission check, and a malformed declaration should not turn every
    assignment into an error far from the class that wrote it.
    """
    here: ptr = cls
    walking: i64 = 1
    while walking:
        if not here:
            walking = 0
        elif i64(load(i32, offset(here, 0))) != apy_type_kind():
            walking = 0
        else:
            d: ptr = ptr(load(u64, offset(here, apy_t_dict_offset())))
            if apy_dict_find_of(
                    d, apy_name_of(rodata(b"__slots__\0"))) < 0:
                return 1
            here = ptr(load(u64, offset(here, apy_t_base_offset())))
    here = cls
    going: i64 = 1
    while going:
        if not here:
            going = 0
        elif i64(load(i32, offset(here, 0))) != apy_type_kind():
            going = 0
        else:
            d2: ptr = ptr(load(u64, offset(here, apy_t_dict_offset())))
            at: i64 = apy_dict_find_of(
                d2, apy_name_of(rodata(b"__slots__\0")))
            if at >= 0:
                vals: ptr = ptr(load(u64, offset(d2, apy_d_vals_offset())))
                names: ptr = ptr(load(u64, offset(
                    vals, at * apy_value_size())))
                n: i64 = apy_raw_len(names)
                if apy_error_occurred():
                    apy_error_clear()
                    return 1
                if i64(load(i32, offset(names, 0))) == apy_str_kind():
                    return apy_eq_raw_of(names, name)
                i: i64 = 0
                while i < n:
                    if apy_eq_raw_of(apy_key_at(names, i), name):
                        return 1
                    i = i + 1
            here = ptr(load(u64, offset(here, apy_t_base_offset())))
    return 0


def apy_default_setattr(obj: ptr, name: ptr, value: ptr) -> ptr:
    """Store `name` on `obj`, the way `object.__setattr__` does.

    A DATA DESCRIPTOR ON THE CLASS TAKES THE WRITE. `c.v = 4` where the class
    has a `property` runs its setter and stores nothing in the instance dict
    -- otherwise the next read would find the stored value and the property
    would never be consulted again.

    FOUR KINDS CARRY A DICT and each makes it on first write: an instance
    always has one, an exception and a function get one only if a program
    hangs something on them, and a class stores through `apy_type_set`
    because a class attribute is not simply a dict entry.
    """
    k: i64 = i64(load(i32, offset(obj, 0)))
    if k == apy_inst_kind():
        cls: ptr = ptr(load(u64, offset(obj, apy_o_cls_offset())))
        if not apy_slot_allows_of(cls, name):
            return apy_raise_fmt(
                rodata(b"AttributeError\0"),
                rodata(b"'%s' object has no attribute '%s' "
                       b"and no __dict__ for setting new attributes\0"),
                apy_kind_name_of(obj),
                ptr(load(u64, offset(name, apy_str_ptr_offset()))))
        found: ptr = apy_class_find_of(cls, name)
        if found:
            handled: i64 = apy_descr_set_of(found, obj, value)
            if handled == 0:
                return ptr(0)
            if handled == 1:
                return apy_none()
        if not apy_dict_set(
                ptr(load(u64, offset(obj, apy_o_dict_offset()))),
                name, value):
            return ptr(0)
        return apy_none()
    if k == apy_exc_kind():
        held: ptr = ptr(load(u64, offset(obj, apy_e_dict_offset())))
        if not held:
            held = apy_dict_new(4)
            if not held:
                return ptr(0)
            store(u64, u64(held), offset(obj, apy_e_dict_offset()))
        if not apy_dict_set(held, name, value):
            return ptr(0)
        return apy_none()
    if k == apy_type_kind():
        return apy_type_set(obj, name, value)
    if k == apy_func_kind():
        fheld: ptr = ptr(load(u64, offset(obj, apy_fn_dict_offset())))
        if not fheld:
            fheld = apy_dict_new(4)
            if not fheld:
                return ptr(0)
            store(u64, u64(fheld), offset(obj, apy_fn_dict_offset()))
        if not apy_dict_set(fheld, name, value):
            return ptr(0)
        return apy_none()
    return apy_raise_fmt(
        rodata(b"AttributeError\0"),
        rodata(b"'%s' object has no attribute '%s'\0"),
        apy_kind_name_of(obj),
        ptr(load(u64, offset(name, apy_str_ptr_offset()))))


def apy_setattr(obj: ptr, name: ptr, value: ptr) -> ptr:
    """`obj.name = value`.

    `__setattr__` INTERCEPTS EVERY assignment, the mirror of
    `__getattribute__`. Asked HERE rather than inside the default so that the
    default stays callable from within the override -- which is what
    `object.__setattr__(self, name, value)` is for, and the only way an
    override can actually store anything.

    `C.__name__ = ...` CHANGES WHAT THE CLASS IS CALLED. The name is a field
    on the type, not an entry in its dict, so storing it as an ordinary
    attribute left `__name__` reading the old one -- the write appeared to
    succeed and changed nothing.

    AND THE EXCEPTION REGISTRATION FOLLOWS THE RENAME. The hierarchy is a
    table of NAMES, so a class renamed after it was registered leaves the two
    disagreeing -- and a bundled module\'s classes are spliced under mangled
    names and then restore `__name__`, precisely so the mangling stays
    invisible. BOTH spellings are kept, because generated code raises through
    the mangled one.
    """
    if i64(load(i32, offset(obj, 0))) == apy_type_kind():
        if i64(load(i32, offset(name, 0))) == apy_str_kind():
            if apy_cstr_eq(
                    ptr(load(u64, offset(name, apy_str_ptr_offset()))),
                    rodata(b"__name__\0")):
                return apy_rename_class(obj, value)
    if i64(load(i32, offset(obj, 0))) == apy_inst_kind():
        hook: ptr = apy_class_find_of(
            ptr(load(u64, offset(obj, apy_o_cls_offset()))),
            apy_name_of(rodata(b"__setattr__\0")))
        if hook:
            argv: ptr = alloca(16)
            store(u64, u64(name), argv)
            store(u64, u64(value), offset(argv, apy_value_size()))
            return apy_call(apy_bind_of(hook, obj), argv, 2)
    return apy_default_setattr(obj, name, value)


def apy_rename_class(obj: ptr, value: ptr) -> ptr:
    """`C.__name__ = "D"`, and the exception table that has to follow it.

    `!found` IS THE CASE THAT MATTERS. An exception class with an EMPTY BODY
    is never handed to `apy_exc_class_bind` at all -- there is nothing to
    build -- so nothing was registered under the mangled name, the
    construction could not find a class, and every display fell back to the
    name the CELL carries. Right for a user\'s class, wrong for a bundled one
    whose cells carry the mangled spelling.
    """
    was: ptr = ptr(load(u64, offset(
        ptr(load(u64, offset(obj, apy_t_name_offset()))),
        apy_str_ptr_offset())))
    parent: ptr = apy_exc_parent_of(was)
    found: ptr = apy_exc_class_named_of(was)
    store(u64, u64(value), offset(obj, apy_t_name_offset()))
    if parent:
        now: ptr = ptr(load(u64, offset(value, apy_str_ptr_offset())))
        if not apy_cstr_eq(was, now):
            apy_exc_register(value, apy_from_cstr(parent))
            if found == obj or not found:
                apy_exc_class_bind(value, obj)
                apy_exc_class_bind(apy_from_cstr(was), obj)
    return apy_none()


def apy_typing_final(obj: ptr) -> ptr:
    """`@final` -- record that this must not be subclassed or overridden.

    A FLAG AND NOTHING ELSE. PEP 591 is a checker\'s rule, not a runtime
    one, and CPython does the same thing: it sets the attribute so a tool can
    read it and lets the program run either way.
    """
    apy_setattr(obj, apy_from_cstr(rodata(b"__final__\0")),
                apy_from_bool(1))
    if apy_error_occurred():
        return ptr(0)
    return obj


def apy_typing_override(obj: ptr) -> ptr:
    """`@override` -- record that this is meant to override a base method.

    PEP 698, and the same bargain `apy_typing_final` makes: the attribute is
    for a checker to read.
    """
    apy_setattr(obj, apy_from_cstr(rodata(b"__override__\0")),
                apy_from_bool(1))
    if apy_error_occurred():
        return ptr(0)
    return obj


def apy_interp_slot() -> ptr:
    """Where the one `Interpolation` class lives once it is built."""
    return reserve("apy_interp_cls_ir", 8)


def apy_interpolation_new(value: ptr, expression: ptr, conversion: ptr,
                          spec: ptr) -> ptr:
    """PEP 750: one `{...}` of a t-string, as an object.

    FOUR FIELDS AND NO METHODS. An interpolation is what a template hands its
    consumer, and everything interesting is done BY that consumer -- so the
    class exists to carry names and nothing else.
    """
    cls: ptr = ptr(load(u64, apy_interp_slot()))
    if not cls:
        cls = apy_type_new(apy_from_cstr(rodata(b"Interpolation\0")),
                           ptr(0))
        if not cls:
            return ptr(0)
        store(u64, u64(cls), apy_interp_slot())
    one: ptr = apy_instance_new(cls)
    if not one:
        return ptr(0)
    apy_setattr(one, apy_from_cstr(rodata(b"value\0")), value)
    apy_setattr(one, apy_from_cstr(rodata(b"expression\0")), expression)
    apy_setattr(one, apy_from_cstr(rodata(b"conversion\0")), conversion)
    apy_setattr(one, apy_from_cstr(rodata(b"format_spec\0")), spec)
    if apy_error_occurred():
        return ptr(0)
    return one


def apy_template_slot() -> ptr:
    """Where the one `Template` class lives once it is built."""
    return reserve("apy_template_cls_ir", 8)


def apy_template_new(strings: ptr, interps: ptr, values: ptr) -> ptr:
    """PEP 750: a t-string, as an object.

    THE LITERAL PIECES AND THE INTERPOLATIONS ARE KEPT APART, which is the
    whole point of a template: a consumer decides what to do with each
    substituted value rather than being handed a finished string.
    """
    cls: ptr = ptr(load(u64, apy_template_slot()))
    if not cls:
        cls = apy_type_new(apy_from_cstr(rodata(b"Template\0")), ptr(0))
        if not cls:
            return ptr(0)
        store(u64, u64(cls), apy_template_slot())
    t: ptr = apy_instance_new(cls)
    if not t:
        return ptr(0)
    apy_setattr(t, apy_from_cstr(rodata(b"strings\0")), strings)
    apy_setattr(t, apy_from_cstr(rodata(b"interpolations\0")), interps)
    apy_setattr(t, apy_from_cstr(rodata(b"values\0")), values)
    if apy_error_occurred():
        return ptr(0)
    return t


def apy_nat_init() -> i64:
    return 1

def apy_nat_new() -> i64:
    return 2

def apy_nat_repr() -> i64:
    return 3

def apy_nat_str() -> i64:
    return 4

def apy_nat_eq() -> i64:
    return 5

def apy_nat_ne() -> i64:
    return 6

def apy_nat_hash() -> i64:
    return 7

def apy_nat_getattr() -> i64:
    return 8

def apy_nat_setattr() -> i64:
    return 9

def apy_nat_delattr() -> i64:
    return 10

def apy_nat_init_subclass() -> i64:
    return 19


# -- the pieces attribute lookup stands on ---------------------------------


def apy_class_builtin_kind(cls: ptr) -> i64:
    """Which builtin kind, if any, this class extends.

    THROUGH THE BASE CHAIN, because `class D(C)` where `class C(dict)` is
    still a dict -- the tag is recorded on the class that named the builtin
    and inherited by everything under it.
    """
    here: ptr = cls
    going: i64 = 1
    while going:
        if not here:
            going = 0
        elif i64(load(i32, offset(here, 0))) != apy_type_kind():
            going = 0
        else:
            k: i64 = i64(load(i32, offset(here, apy_t_builtin_offset())))
            if k:
                return k
            here = ptr(load(u64, offset(here, apy_t_base_offset())))
    return 0


def apy_is_descriptor_of(v: ptr) -> i64:
    """Does `v` want to intercept a READ?

    ANY `__get__` COUNTS, unlike `apy_is_data_descriptor_of` which wants
    `__set__`: a plain method is a descriptor for reading and loses to the
    instance dict for writing, which is the whole non-data/data distinction.
    """
    if i64(load(i32, offset(v, 0))) == apy_prop_kind():
        return 1
    if i64(load(i32, offset(v, 0))) != apy_inst_kind():
        return 0
    if apy_class_find_of(ptr(load(u64, offset(v, apy_o_cls_offset()))),
                         apy_name_of(rodata(b"__get__\0"))):
        return 1
    return 0


def apy_member_slot() -> ptr:
    """Where the one `member_descriptor` class lives."""
    return reserve("apy_member_cls_ir", 8)


def apy_member_descriptor() -> ptr:
    """One `member_descriptor`, which is what a slot reads as on the CLASS.

    `C.x` FOR A SLOTTED CLASS IS NOT THE VALUE -- there is no instance to
    read it from -- and CPython answers a descriptor object. Answering the
    slot's value would be answering some other instance's.
    """
    held: ptr = ptr(load(u64, apy_member_slot()))
    if not held:
        held = apy_type_new(
            apy_from_cstr(rodata(b"member_descriptor\0")), ptr(0))
        if not held:
            return held
        store(u64, u64(held), apy_member_slot())
    return apy_instance_new(held)


def apy_kind_class_slot() -> ptr:
    """The cache of one class per builtin kind name."""
    return reserve("apy_kind_class_ir", 8)


def apy_kind_class(obj: ptr) -> ptr:
    """The class object standing for `obj`'s builtin kind.

    ONE PER NAME, remembered: `type(1) is type(2)` has to hold, and a fresh
    cell per lookup would make it False.
    """
    key: ptr = apy_from_cstr(apy_kind_name_of(obj))
    slot: ptr = apy_kind_class_slot()
    classes: ptr = ptr(load(u64, slot))
    if not classes:
        classes = apy_dict_new(8)
        if not classes:
            return classes
        store(u64, u64(classes), slot)
    found: ptr = apy_dict_get_or(classes, key, ptr(0))
    if found:
        return found
    found = apy_type_new(key, ptr(0))
    apy_dict_set(classes, key, found)
    return found


def apy_object_default(want: ptr) -> ptr:
    """`object`'s own implementation of a dunder, by name.

    ELEVEN NAMES AND NO MORE. These are what `super().__repr__()` reaches
    from a class that overrode it, and what `object` carries so that
    `hasattr(x, "__eq__")` is True for everything.
    """
    if apy_cstr_eq(want, rodata(b"__init__\0")):
        return apy_native_of(apy_nat_init(), 1, rodata(b"__init__\0"))
    if apy_cstr_eq(want, rodata(b"__new__\0")):
        return apy_native_of(apy_nat_new(), 1, rodata(b"__new__\0"))
    if apy_cstr_eq(want, rodata(b"__repr__\0")):
        return apy_native_of(apy_nat_repr(), 1, rodata(b"__repr__\0"))
    if apy_cstr_eq(want, rodata(b"__str__\0")):
        return apy_native_of(apy_nat_str(), 1, rodata(b"__str__\0"))
    if apy_cstr_eq(want, rodata(b"__eq__\0")):
        return apy_native_of(apy_nat_eq(), 2, rodata(b"__eq__\0"))
    if apy_cstr_eq(want, rodata(b"__ne__\0")):
        return apy_native_of(apy_nat_ne(), 2, rodata(b"__ne__\0"))
    if apy_cstr_eq(want, rodata(b"__hash__\0")):
        return apy_native_of(apy_nat_hash(), 1, rodata(b"__hash__\0"))
    if apy_cstr_eq(want, rodata(b"__getattribute__\0")):
        return apy_native_of(apy_nat_getattr(), 2,
                             rodata(b"__getattribute__\0"))
    if apy_cstr_eq(want, rodata(b"__setattr__\0")):
        return apy_native_of(apy_nat_setattr(), 3,
                             rodata(b"__setattr__\0"))
    if apy_cstr_eq(want, rodata(b"__delattr__\0")):
        return apy_native_of(apy_nat_delattr(), 2,
                             rodata(b"__delattr__\0"))
    if apy_cstr_eq(want, rodata(b"__init_subclass__\0")):
        return apy_native_of(apy_nat_init_subclass(), 1,
                             rodata(b"__init_subclass__\0"))
    return ptr(0)


def apy_object_slot() -> ptr:
    """Where the one `object` class lives once it is built."""
    return reserve("apy_object_cls_ir", 8)


def apy_object_class() -> ptr:
    """The class `object` itself is, made once and filled with its dunders."""
    held: ptr = ptr(load(u64, apy_object_slot()))
    if held:
        return held
    cls: ptr = apy_type_new(apy_from_cstr(rodata(b"object\0")), ptr(0))
    if not cls:
        return cls
    store(u64, u64(cls), apy_object_slot())
    d: ptr = ptr(load(u64, offset(cls, apy_t_dict_offset())))
    apy_object_fill(d, rodata(b"__init__\0"))
    apy_object_fill(d, rodata(b"__new__\0"))
    apy_object_fill(d, rodata(b"__repr__\0"))
    apy_object_fill(d, rodata(b"__str__\0"))
    apy_object_fill(d, rodata(b"__eq__\0"))
    apy_object_fill(d, rodata(b"__ne__\0"))
    apy_object_fill(d, rodata(b"__hash__\0"))
    apy_object_fill(d, rodata(b"__getattribute__\0"))
    apy_object_fill(d, rodata(b"__setattr__\0"))
    apy_object_fill(d, rodata(b"__delattr__\0"))
    return cls


def apy_object_fill(d: ptr, name: ptr) -> None:
    """Put one of `object`'s defaults into its dict, under its own name."""
    apy_dict_set(d, apy_name_of(name), apy_object_default(name))


def apy_descr_get_of(d: ptr, obj: ptr, cls: ptr) -> ptr:
    """Read through a descriptor.

    A `property` READ ON THE CLASS ANSWERS THE DESCRIPTOR, not a value:
    `C.v` has no instance to compute from, and CPython hands back the
    property object so `C.v.setter` works.

    A `classmethod` BINDS THE CLASS and a `staticmethod` binds nothing,
    which is the whole difference between the two.
    """
    if i64(load(i32, offset(d, 0))) == apy_prop_kind():
        kind: i64 = i64(load(i32, offset(d, apy_prop_kind_offset())))
        getter: ptr = ptr(load(u64, offset(d, apy_prop_get_offset())))
        if kind == apy_prop_kind_property():
            if not obj:
                return d
            if not getter:
                return apy_raise_at(rodata(b"AttributeError\0"),
                                    rodata(b"unreadable attribute\0"))
            one: ptr = alloca(8)
            store(u64, u64(obj), one)
            return apy_call(getter, one, 1)
        if kind == apy_prop_classmethod():
            return apy_bind_of(getter, cls)
        return getter
    m: ptr = apy_class_find_of(
        ptr(load(u64, offset(d, apy_o_cls_offset()))),
        apy_name_of(rodata(b"__get__\0")))
    argv: ptr = alloca(16)
    who: ptr = obj
    if not who:
        who = apy_none()
    what: ptr = cls
    if not what:
        what = apy_none()
    store(u64, u64(who), argv)
    store(u64, u64(what), offset(argv, apy_value_size()))
    return apy_call(apy_bind_of(m, d), argv, 2)


# -- what a BUILTIN answers to, which is a table and not a class ------------
#
# A builtin kind has no class object to look a name up in: the methods live in
# the frontend's dispatch table, which is a compile-time fact. So when a
# program asks for one as a VALUE -- `[].append`, `d.keys`, `x.__len__` -- the
# answer has to be synthesised, and this is where the list of what exists
# lives.


def apy_kind_method_of(obj: ptr, arity: i64, name: ptr, bind: i64) -> ptr:
    """One builtin method, as a callable value, optionally bound.

    `APY_NAT_KIND` STANDS FOR "WHATEVER THIS KIND'S IS", which is why it is
    never cached: two callers asking for it mean different functions.
    """
    fn: ptr = apy_native_of(apy_nat_kind(), arity, name)
    if bind:
        return apy_bind_of(fn, obj)
    return fn


def apy_kind_is(obj: ptr, kind: i64) -> i64:
    """`obj`'s kind tag against one value, as an i64.

    AN i64 AND NOT A BOOL, because the tests below combine these with
    `apy_is_seq_of` and its siblings -- which answer i64 -- and the subset
    will not mix the two widths in one `and`. Written once here rather than
    at each of the twenty places that needs it.
    """
    if i64(load(i32, offset(obj, 0))) == kind:
        return 1
    return 0


def apy_name_is(want: ptr, name: ptr) -> i64:
    """A C-string comparison, as an i64. See `apy_kind_is`."""
    if apy_cstr_eq(want, name):
        return 1
    return 0


def apy_kind_attr_of(obj: ptr, want: ptr, bind: i64) -> ptr:
    """The builtin method or field `want` names on `obj`, or null.

    GATED BY KIND, every one of them: `[].keys` has to be an AttributeError
    and `{}.keys` a method, and the only thing that separates them here is
    which test the name sits behind.

    `__hash__` EXISTS EITHER WAY and answers None for a mutable kind, because
    `[].__hash__ is None` is how a program asks whether a list can be a dict
    key -- "no such attribute" is a different claim from the one CPython
    makes.
    """
    seq: i64 = apy_is_seq_of(obj)
    sset: i64 = apy_is_set_of(obj)
    is_str: i64 = apy_kind_is(obj, apy_str_kind())
    is_bytes: i64 = apy_kind_is(obj, apy_bytes_kind())
    is_dict: i64 = apy_kind_is(obj, apy_dict_kind())
    is_list: i64 = apy_kind_is(obj, apy_list_kind())
    is_set: i64 = apy_kind_is(obj, apy_set_kind())
    is_mview: i64 = apy_kind_is(obj, apy_mview_kind())
    is_view: i64 = apy_kind_is(obj, apy_view_kind())
    is_gen: i64 = apy_kind_is(obj, apy_gen_kind())
    is_iter: i64 = apy_kind_is(obj, apy_iter_kind())
    is_range: i64 = apy_kind_is(obj, apy_range_kind())
    text: i64 = is_str or is_bytes
    walks: i64 = seq or sset or text or is_dict or is_mview or is_view
    writable_bytes: i64 = 0
    if is_bytes:
        if load(i32, offset(obj, apy_s_mut_offset())):
            writable_bytes = 1
    mut: i64 = is_list or is_dict or is_set or writable_bytes
    if apy_name_is(want, rodata(b"__hash__\0")):
        if mut:
            return apy_none()
        return apy_kind_method_of(obj, 1, rodata(b"__hash__\0"), bind)
    if apy_name_is(want, rodata(b"__len__\0")) and walks:
        return apy_kind_method_of(obj, 1, rodata(b"__len__\0"), bind)
    if apy_name_is(want, rodata(b"__iter__\0")):
        if walks or is_gen or is_iter:
            return apy_kind_method_of(obj, 1, rodata(b"__iter__\0"), bind)
    if apy_name_is(want, rodata(b"__next__\0")):
        if is_gen or is_iter:
            return apy_kind_method_of(obj, 1, rodata(b"__next__\0"), bind)
    if apy_name_is(want, rodata(b"__contains__\0")) and walks:
        return apy_kind_method_of(obj, 2, rodata(b"__contains__\0"), bind)
    if apy_name_is(want, rodata(b"__getitem__\0")):
        if seq or text or is_dict or is_mview:
            return apy_kind_method_of(obj, 2, rodata(b"__getitem__\0"),
                                      bind)
    if apy_name_is(want, rodata(b"__setitem__\0")):
        if is_list or is_dict or writable_bytes:
            return apy_kind_method_of(obj, 3, rodata(b"__setitem__\0"),
                                      bind)
    if is_dict:
        if apy_name_is(want, rodata(b"keys\0")):
            return apy_kind_method_of(obj, 1, want, bind)
        if apy_name_is(want, rodata(b"values\0")):
            return apy_kind_method_of(obj, 1, want, bind)
        if apy_name_is(want, rodata(b"items\0")):
            return apy_kind_method_of(obj, 1, want, bind)
    if seq or text:
        if apy_name_is(want, rodata(b"index\0")):
            return apy_kind_method_of(obj, 2, want, bind)
        if apy_name_is(want, rodata(b"count\0")):
            return apy_kind_method_of(obj, 2, want, bind)
    if is_list:
        if apy_name_is(want, rodata(b"append\0")):
            return apy_kind_method_of(obj, 2, rodata(b"append\0"), bind)
        if apy_name_is(want, rodata(b"insert\0")):
            return apy_kind_method_of(obj, 3, rodata(b"insert\0"), bind)
    if is_set:
        if apy_name_is(want, rodata(b"add\0")):
            return apy_kind_method_of(obj, 2, want, bind)
        if apy_name_is(want, rodata(b"discard\0")):
            return apy_kind_method_of(obj, 2, want, bind)
    if sset and apy_name_is(want, rodata(b"isdisjoint\0")):
        return apy_kind_method_of(obj, 2, rodata(b"isdisjoint\0"), bind)
    if apy_name_is(want, rodata(b"__buffer__\0")):
        if is_bytes or is_mview:
            return apy_kind_method_of(obj, 2, rodata(b"__buffer__\0"),
                                      bind)
    if is_range:
        if apy_name_is(want, rodata(b"start\0")):
            return apy_from_int(load(i64, offset(obj,
                                                 apy_rg_start_offset())))
        if apy_name_is(want, rodata(b"stop\0")):
            return apy_from_int(load(i64, offset(obj, apy_rg_stop_offset())))
        if apy_name_is(want, rodata(b"step\0")):
            return apy_from_int(load(i64, offset(obj, apy_rg_step_offset())))
        if apy_name_is(want, rodata(b"index\0")):
            return apy_kind_method_of(obj, 2, want, bind)
        if apy_name_is(want, rodata(b"count\0")):
            return apy_kind_method_of(obj, 2, want, bind)
        if apy_name_is(want, rodata(b"__len__\0")):
            return apy_kind_method_of(obj, 1, rodata(b"__len__\0"), bind)
        if apy_name_is(want, rodata(b"__iter__\0")):
            return apy_kind_method_of(obj, 1, rodata(b"__iter__\0"), bind)
        if apy_name_is(want, rodata(b"__contains__\0")):
            return apy_kind_method_of(obj, 2, rodata(b"__contains__\0"),
                                      bind)
        if apy_name_is(want, rodata(b"__getitem__\0")):
            return apy_kind_method_of(obj, 2, rodata(b"__getitem__\0"),
                                      bind)
    return ptr(0)


def apy_kind_attr(obj: ptr, want: ptr) -> ptr:
    """`apy_kind_attr_of` with the result BOUND, which is what a read wants."""
    return apy_kind_attr_of(obj, want, 1)


def apy_kind_prototype(type_name: ptr) -> ptr:
    """An empty value of the kind `type_name` names, or null.

    WHAT A BUILTIN TYPE USED AS A VALUE ANSWERS ATTRIBUTES FROM. `list.append`
    has no list to ask, so one is made -- empty, thrown away, and only ever
    used to decide which methods that kind has.
    """
    if apy_name_is(type_name, rodata(b"list\0")):
        return apy_list_new(1)
    if apy_name_is(type_name, rodata(b"tuple\0")):
        return apy_tuple_new(1)
    if apy_name_is(type_name, rodata(b"dict\0")):
        return apy_dict_new(1)
    if apy_name_is(type_name, rodata(b"set\0")):
        return apy_set_new(1)
    if apy_name_is(type_name, rodata(b"frozenset\0")):
        return apy_frozenset_new(1)
    if apy_name_is(type_name, rodata(b"str\0")):
        return apy_from_cstr(rodata(b"\0"))
    if apy_name_is(type_name, rodata(b"bytes\0")):
        return apy_bytes_literal(rodata(b"\0"), 0)
    if apy_name_is(type_name, rodata(b"int\0")):
        return apy_from_int(0)
    if apy_name_is(type_name, rodata(b"bool\0")):
        return apy_from_int(0)
    if apy_name_is(type_name, rodata(b"float\0")):
        return apy_from_float(f64(0))
    return ptr(0)


def apy_no_attribute(obj: ptr, name: ptr) -> ptr:
    """The last thing attribute lookup tries, and the error if it fails.

    A BUILTIN TYPE NAME IS ASKED THROUGH A PROTOTYPE: `list.append` is a real
    attribute, and the only way to answer it is to make an empty list and ask
    THAT what it has. Nothing else knows which methods a kind carries.
    """
    want: ptr = ptr(load(u64, offset(name, apy_str_ptr_offset())))
    found: ptr = apy_kind_attr(obj, want)
    if found:
        return found
    if i64(load(i32, offset(obj, 0))) == apy_func_kind():
        if load(i32, offset(obj, apy_fn_is_type_offset())):
            proto: ptr = apy_kind_prototype(ptr(load(u64, offset(
                ptr(load(u64, offset(obj, apy_fn_name_offset()))),
                apy_str_ptr_offset()))))
            if proto:
                found = apy_kind_attr_of(proto, want, 0)
                if found:
                    return found
    return apy_raise_fmt(
        rodata(b"AttributeError\0"),
        rodata(b"'%s' object has no attribute '%s'\0"),
        apy_kind_name_of(obj), want)


def apy_mro_entries(written: ptr, bases: ptr) -> ptr:
    """PEP 560: what a non-class written as a base RESOLVES to.

    `class C(Generic[T])` names something that is not a class, and
    `__mro_entries__` is how it says what should stand in its place. A
    generic alias answers `Generic`; anything without the hook is simply not
    a base.

    THE FIRST ENTRY IS TAKEN and the rest dropped, which is a stated
    simplification: the full protocol substitutes the whole sequence into the
    base list, and one base is what every use here needs.
    """
    if i64(load(i32, offset(written, 0))) == apy_type_kind():
        return written
    if i64(load(i32, offset(written, 0))) != apy_inst_kind():
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"bases must be types, not '%s'%s\0"),
            apy_kind_name_of(written), rodata(b"\0"))
    hook: ptr = apy_class_find_of(
        ptr(load(u64, offset(written, apy_o_cls_offset()))),
        apy_name_of(rodata(b"__mro_entries__\0")))
    if not hook:
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"bases must be types, not '%s'%s\0"),
            apy_kind_name_of(written), rodata(b"\0"))
    one: ptr = alloca(8)
    store(u64, u64(bases), one)
    got: ptr = apy_call(apy_bind_of(hook, written), one, 1)
    if not got:
        return ptr(0)
    if apy_is_seq_of(got):
        if load(i64, offset(got, apy_q_n_offset())) > 0:
            return ptr(load(u64, ptr(load(u64, offset(
                got, apy_q_items_offset())))))
    return apy_object_class()


# -- reading an attribute off an instance -----------------------------------
#
# SPLIT, and the line is drawn at the KIND. `apy_default_getattr` answers ten
# of them -- a class, a super, a slice, a property, a function, a generic
# alias, a generator, a memoryview, a complex, an exception -- and each is a
# page of its own field names. This half answers the one a program actually
# spends its time in: `self.x` on an instance.
#
# EVERYTHING ELSE GOES BACK TO THE C, which is the same bargain
# `apy_num_order_of` makes for bigs and `apy_call` makes for keyword matching:
# the shape that runs in a loop is ported, and the shapes that run once keep
# working exactly as they did.


def apy_inst_getattr(obj: ptr, name: ptr) -> ptr:
    """`object.__getattribute__(obj, name)` for an instance.

    THIS IS THE DEFAULT LOOKUP, not the entry point. A class overriding
    `__getattribute__` is asked first by `apy_getattr` and reaches this by
    calling the default explicitly, which is the only way out of the
    recursion.

    A DATA DESCRIPTOR ON THE CLASS BEATS THE INSTANCE DICT, and it is the one
    place the "instance wins" rule does not hold -- it is what makes a
    `property` a property: `c.v = 4` runs its setter and the instance dict
    never gets a `v` to shadow it with. A NON-data descriptor loses to the
    dict instead, which is how a method can be shadowed by an attribute of
    the same name.

    A FUNCTION FOUND ON THE CLASS BINDS AND ANYTHING ELSE DOES NOT. That
    single test is the whole of the "methods take self" rule.

    A HELD BUILTIN IS ASKED AFTER THE CLASS AND BEFORE `__getattr__`. The
    class body wins -- a `Counter` defining `update` shadows `dict.update` --
    and the fallback loses, because in CPython these arrive through the MRO,
    which is consulted first. THE MISS IS NOT THE ANSWER: a name neither has
    must still reach `__getattr__`, so the AttributeError the delegation
    raised is cleared rather than reported.
    """
    cls: ptr = ptr(load(u64, offset(obj, apy_o_cls_offset())))
    d: ptr = ptr(load(u64, offset(obj, apy_o_dict_offset())))
    found: ptr = apy_class_find_of(cls, name)
    if found:
        if apy_is_data_descriptor_of(found):
            return apy_descr_get_of(found, obj, cls)
    at: i64 = apy_dict_find_of(d, name)
    if at >= 0:
        vals: ptr = ptr(load(u64, offset(d, apy_d_vals_offset())))
        return ptr(load(u64, offset(vals, at * apy_value_size())))
    if found:
        if apy_is_descriptor_of(found):
            return apy_descr_get_of(found, obj, cls)
        if i64(load(i32, offset(found, 0))) == apy_func_kind():
            return apy_bind_of(found, obj)
        return found
    want: ptr = ptr(load(u64, offset(name, apy_str_ptr_offset())))
    if apy_cstr_eq(want, rodata(b"__class__\0")):
        return cls
    if apy_cstr_eq(want, rodata(b"__dict__\0")):
        # ABSENT UNDER `__slots__`, which is the point of declaring it --
        # `hasattr(p, "__dict__")` is how a program checks. The dict ITSELF
        # and not a copy: `obj.__dict__["x"] = 1` is how an attribute is set
        # dynamically, and a copy would accept the write and lose it.
        if not apy_slot_allows_of(cls, apy_from_cstr(
                rodata(b"__dict__\0"))):
            return apy_no_attribute(obj, name)
        return d
    held: ptr = ptr(load(u64, offset(obj, apy_o_held_offset())))
    if held:
        got: ptr = apy_default_getattr(held, name)
        if got:
            return got
        if apy_cstr_eq(apy_err_kind(), rodata(b"AttributeError\0")):
            apy_error_clear()
        else:
            return got
    hook: ptr = apy_class_find_of(cls,
                                  apy_name_of(rodata(b"__getattr__\0")))
    if hook:
        one: ptr = alloca(8)
        store(u64, u64(name), one)
        return apy_call(apy_bind_of(hook, obj), one, 1)
    return apy_no_attribute(obj, name)


def apy_default_getattr(obj: ptr, name: ptr) -> ptr:
    """`object.__getattribute__` -- the default read, for every kind."""
    if i64(load(i32, offset(obj, 0))) == apy_inst_kind():
        return apy_inst_getattr(obj, name)
    return apy_default_getattr_slow(obj, name)


def apy_getattr(obj: ptr, name: ptr) -> ptr:
    """`obj.name` -- the entry point, with `__getattribute__` in front of it.

    `__getattribute__` INTERCEPTS EVERYTHING, which is the whole difference
    from `__getattr__`: one is asked before the lookup and sees every name,
    the other only after it has missed. A class overriding it reaches the
    ordinary rules by calling `object.__getattribute__` explicitly, which is
    the only way out of the recursion.

    ONLY THE NAME IS PASSED. The hook is bound to the receiver already, so
    handing it `obj` as well would put the object in front of its own
    argument and every name would arrive one place late.
    """
    if i64(load(i32, offset(obj, 0))) == apy_inst_kind():
        hook: ptr = apy_class_find_of(
            ptr(load(u64, offset(obj, apy_o_cls_offset()))),
            apy_name_of(rodata(b"__getattribute__\0")))
        if hook:
            one: ptr = alloca(8)
            store(u64, u64(name), one)
            return apy_call(apy_bind_of(hook, obj), one, 1)
    return apy_default_getattr(obj, name)


def apy_getattr_default(obj: ptr, name: ptr, fallback: ptr) -> ptr:
    """`getattr(obj, name, default)`.

    ONLY AN AttributeError IS SWALLOWED. A `__getattr__` that raises
    something else -- a ValueError from a computed property, say -- must
    reach the caller: `getattr(x, "n", 0)` asks whether the attribute is
    there, not whether reading it worked.
    """
    got: ptr = apy_getattr(obj, name)
    if got:
        return got
    if apy_error_matches(apy_from_cstr(rodata(b"AttributeError\0"))):
        apy_error_clear()
        return fallback
    return ptr(0)


def apy_hasattr(v: ptr, name: ptr) -> ptr:
    """`hasattr(obj, name)`.

    ONLY AN AttributeError IS SWALLOWED, which is Python's rule since 3.2 --
    before that `hasattr` caught everything, and the change was made because
    a property raising a ValueError read as "no such attribute" and hid a
    real failure. This caught everything too, and answered False where
    CPython propagates.
    """
    got: ptr = apy_getattr(v, name)
    if got:
        return apy_from_bool(1)
    if apy_error_matches(apy_from_cstr(rodata(b"AttributeError\0"))):
        apy_error_clear()
        return apy_from_bool(0)
    return ptr(0)


def apy_typing_form_slot() -> ptr:
    """The cache of one interned form per name."""
    return reserve("apy_typing_form_ir", 8)


def apy_typing_form(name: ptr) -> ptr:
    """One of the interned typing forms -- `Literal`, `TypeGuard`, their kin.

    INTERNED BY NAME, because a program compares them by identity: `x is
    Literal` is how a checker-shaped library asks which form it holds, and a
    fresh instance per mention would answer False.
    """
    slot: ptr = apy_typing_form_slot()
    seen: ptr = ptr(load(u64, slot))
    if not seen:
        seen = apy_dict_new(8)
        if not seen:
            return seen
        store(u64, u64(seen), slot)
    found: ptr = apy_dict_get_or(seen, name, ptr(0))
    if found:
        return found
    cls: ptr = apy_special_form_class()
    if not cls:
        return cls
    o: ptr = apy_instance_new(cls)
    if not o:
        return o
    apy_setattr(o, apy_from_cstr(rodata(b"_name\0")), name)
    if apy_error_occurred():
        return ptr(0)
    apy_dict_set(seen, name, o)
    return o
