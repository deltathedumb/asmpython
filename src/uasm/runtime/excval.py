# Building an exception object, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md. `runtime/errstate.py` owns the pending
# error -- its type, its message, its position -- and this is what turns that
# into the object a handler binds with `except E as e`.


# THE NUMBERS BELOW ARE THE C COMPILER'S. See `runtime/slots.py`.


def apy_e_dict_offset() -> i64:
    return 64


def apy_e_cls_offset() -> i64:
    return 72


def apy_e_pos_offset() -> i64:
    return 80


def apy_e_subs_offset() -> i64:
    return 88


def apy_e_rendered_offset() -> i64:
    return 100


def apy_e_arg_offset() -> i64:
    return 16


def apy_e_has_arg_offset() -> i64:
    return 24


def apy_e_notes_offset() -> i64:
    return 56


def apy_t_meta_offset() -> i64:
    return 32


def apy_error_type() -> ptr:
    """The pending error's type name as a string, or None.

    THROUGH `apy_from_cstr` RATHER THAN `apy_lit`, which is the same function:
    `apy_lit` is the C's name for it and now delegates here. See the note on
    `apy_lit` in `objects/c/_core.py`.
    """
    if not apy_err_kind():
        return apy_none()
    return apy_from_cstr(apy_err_kind())


def apy_error_value() -> ptr:
    """The pending error as an object, built if it was never one.

    AN OPERATION THAT FAILS LEAVES NO OBJECT -- it leaves a type name and a
    message, which is all a report needs and all the C ever kept. A `raise`
    does leave one, and that one is handed straight back: rebuilding it would
    turn `raise E(42)` into an `E('42')`, a different value of a different
    type, because only the rendered TEXT survives the round trip.

    `rendered` MARKS THE DIFFERENCE and is not decoration. A KeyError built
    here already holds the repr of its key, so repr'ing it again would give
    `KeyError("'k'")` where Python says `KeyError('k')` -- the flag is how
    `apy_text` knows not to quote twice.

    THE POSITION IS THE LATCHED ONE, not the current one: by the time anything
    asks for this object, the handler's own statements have moved the cursor.
    """
    if not apy_err_kind():
        return apy_none()
    held: ptr = apy_err_obj()
    if held:
        return held
    o: ptr = apy_obj_alloc(apy_exc_kind())
    if not o:
        return o
    store(u64, u64(apy_err_kind()), offset(o, apy_e_name_offset()))
    store(i64, apy_pos_latched(), offset(o, apy_e_pos_offset()))
    store(i32, i32(1), offset(o, apy_e_rendered_offset()))
    text: ptr = apy_err_text()
    if load(u8, text) != u8(0):
        n: i64 = 0
        while load(u8, offset(text, n)) != u8(0):
            n = n + 1
        store(u64, u64(apy_str_copy_bytes(text, n)),
              offset(o, apy_e_arg_offset()))
        store(i32, i32(1), offset(o, apy_e_has_arg_offset()))
    else:
        store(u64, u64(apy_none()), offset(o, apy_e_arg_offset()))
    return o


def apy_add_note(exc: ptr, text: ptr) -> ptr:
    """`e.add_note(text)` -- PEP 678.

    THE LIST IS MADE ON FIRST USE, because most exceptions never get a note
    and a list per exception would be an allocation per raise.
    """
    if i64(load(i32, offset(exc, 0))) != apy_exc_kind():
        return apy_raise_fmt(
            rodata(b"AttributeError\0"),
            rodata(b"'%s' object has no attribute 'add_note'%s\0"),
            apy_kind_name_of(exc), rodata(b"\0"))
    if not apy_is_str(text):
        # CPYTHON'S WORDING, WHICH THE C DID NOT HAVE: it said
        # "note must be a str", naming neither the method nor the
        # kind it was handed. Both halves say this now.
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"add_note() argument must be str, not %s%s\0"),
            apy_kind_name_of(text), rodata(b"\0"))
    notes: ptr = ptr(load(u64, offset(exc, apy_e_notes_offset())))
    if not notes:
        notes = apy_list_new(2)
        if not notes:
            return notes
        store(u64, u64(notes), offset(exc, apy_e_notes_offset()))
    apy_seq_push(notes, text)
    return apy_none()


def apy_e_context_offset() -> i64:
    return 40


def apy_raise_text() -> ptr:
    """Where a raised exception\'s message is assembled.

    RESERVED AND NOT `alloca`, because the only thing that reads it is
    `apy_raise_over`, called on the next line: the buffer is dead before
    anything that could reach this again.
    """
    return reserve("apy_raise_text_ir", 256)


def apy_raise(exc: ptr) -> ptr:
    """`raise exc` -- record it as pending and answer 0.

    SPLIT. The fast path takes an exception whose argument is ALREADY a
    string, or which has none -- which is what `raise ValueError("x")` and
    `raise KeyError` are. Anything else needs `apy_str` to render the
    argument, which is polymorphic over every kind there is, so it goes back
    to the C.

    `__context__` IS SET HERE AND NOT AT THE `except`, because only a raise
    creates a link. It is set even when a `from` clause suppressed it:
    `raise X from Y` records BOTH -- the cause is what to PRINT and
    `__suppress_context__` is whether to print the context, not whether to
    have one.

    A RAISE WHILE AN ERROR IS STILL PENDING chains too -- `try: raise A
    finally: raise B` -- and nothing was "being handled" there: the A is in
    flight rather than caught. Taken before `apy_raise_over` clears the cell,
    which is the only moment it exists.

    THE TRACEBACK IS BORN HERE. An exception carries no position until this
    runs, which is what makes `ValueError("x").__traceback__` None and the
    same object\'s traceback real once it has been raised.

    THE OBJECT IS STORED AFTER `apy_raise_over`, which clears it: the text is
    what an uncaught error reports and what a handler matches on, and the
    object is what `except ... as e` binds. Both, not either.
    """
    if i64(load(i32, offset(exc, 0))) != apy_exc_kind():
        return apy_raise_slow(exc)
    has: i64 = i64(load(i32, offset(exc, apy_e_has_arg_offset())))
    if has:
        arg: ptr = ptr(load(u64, offset(exc, apy_e_arg_offset())))
        if not arg:
            return apy_raise_slow(exc)
        if i64(load(i32, offset(arg, 0))) != apy_str_kind():
            return apy_raise_slow(exc)
    if not ptr(load(u64, offset(exc, apy_e_context_offset()))):
        held: ptr = apy_handling_now()
        if held:
            if held != exc:
                store(u64, u64(held), offset(exc, apy_e_context_offset()))
    if not ptr(load(u64, offset(exc, apy_e_context_offset()))):
        if apy_err_kind():
            pending: ptr = apy_error_value()
            if pending:
                if pending != exc:
                    if i64(load(i32, offset(pending, 0))) == apy_exc_kind():
                        store(u64, u64(pending),
                              offset(exc, apy_e_context_offset()))
    store(i64, apy_pos_now(), offset(exc, apy_e_pos_offset()))
    name: ptr = ptr(load(u64, offset(exc, apy_e_name_offset())))
    if not has:
        apy_raise_over(name, rodata(b"\0"))
        apy_err_set_obj(exc)
        return ptr(0)
    shown: ptr = ptr(load(u64, offset(exc, apy_e_arg_offset())))
    buf: ptr = apy_raise_text()
    n: i64 = load(i64, offset(shown, apy_str_len_offset()))
    if n > 255:
        n = 255
    src: ptr = ptr(load(u64, offset(shown, apy_str_ptr_offset())))
    i: i64 = 0
    while i < n:
        store(u8, load(u8, offset(src, i)), offset(buf, i))
        i = i + 1
    store(u8, u8(0), offset(buf, n))
    apy_raise_over(name, buf)
    apy_err_set_obj(exc)
    return ptr(0)


def apy_exc_class_named_of(name: ptr) -> ptr:
    """The class a program declared for exceptions of this NAME, or null.

    BY NAME AND NOT BY OBJECT, because that is all the raising side has --
    see `apy_exc_class_bind`, which fills the table this reads.
    """
    held: ptr = ptr(load(u64, apy_exc_class_slot()))
    if not held:
        return ptr(0)
    return apy_dict_get_or(held, apy_from_cstr(name), ptr(0))


def apy_exc_construct_of(exc: ptr, args: ptr, n: i64) -> ptr:
    """Run a program-written exception class\'s `__init__` over a raised cell.

    A BUILTIN EXCEPTION HAS NO CLASS TO RUN and comes back untouched, which
    is the common case: `raise ValueError("x")` never reaches a Python body.

    THE CELL IS ITS OWN FIRST ARGUMENT, because `__init__` is a method and
    the exception is `self` -- which is also why the class is recorded on the
    cell BEFORE the call, so a body that reads `type(self)` sees the right
    answer.

    EIGHT ARGUMENTS AND NO MORE, the C\'s limit and kept: the array is a
    fixed one and an exception constructor with nine parameters is not a
    shape this has to answer.
    """
    cls: ptr = apy_exc_class_named_of(
        ptr(load(u64, offset(exc, apy_e_name_offset()))))
    if not cls:
        return exc
    store(u64, u64(cls), offset(exc, apy_e_cls_offset()))
    init: ptr = apy_class_find_of(cls, apy_name_of(rodata(b"__init__\0")))
    if not init:
        return exc
    if i64(load(i32, offset(init, 0))) != apy_func_kind():
        return exc
    if n > 8:
        n = 8
    argv: ptr = alloca(72)
    store(u64, u64(exc), argv)
    i: i64 = 0
    while i < n:
        store(u64, load(u64, offset(args, i * apy_value_size())),
              offset(argv, (i + 1) * apy_value_size()))
        i = i + 1
    if not apy_call(init, argv, n + 1):
        return ptr(0)
    return exc


def apy_make_exc(type_name: ptr, arg: ptr) -> ptr:
    """Build an exception cell of `type_name` carrying `arg`.

    THE NAME IS KEPT AS A C STRING and not as the value handed in, because
    everything downstream matches on it that way -- the handler test, the
    class table, the report for an uncaught one.

    THE POSITION IS -1 UNTIL IT IS RAISED, which is what makes
    `ValueError("x").__traceback__` None: an exception that was built and
    never raised has no place to point at.
    """
    o: ptr = apy_obj_alloc(apy_exc_kind())
    if not o:
        return o
    store(i64, -1, offset(o, apy_e_pos_offset()))
    store(u64, u64(ptr(load(u64, offset(type_name, apy_str_ptr_offset())))),
          offset(o, apy_e_name_offset()))
    store(u64, u64(arg), offset(o, apy_e_arg_offset()))
    store(i32, i32(1), offset(o, apy_e_has_arg_offset()))
    one: ptr = alloca(8)
    store(u64, u64(arg), one)
    return apy_exc_construct_of(o, one, 1)


def apy_e_cause_offset() -> i64:
    return 48


def apy_e_suppress_offset() -> i64:
    return 96


def apy_make_exc0(type_name: ptr) -> ptr:
    """An exception of `type_name` carrying NO argument.

    `E()` AND `E(None)` ARE DIFFERENT EXCEPTIONS -- `e.args` is `()` for the
    first and `(None,)` for the second -- which is why this exists beside
    `apy_make_exc` rather than being it with None passed in.
    """
    o: ptr = apy_obj_alloc(apy_exc_kind())
    if not o:
        return o
    store(i64, -1, offset(o, apy_e_pos_offset()))
    store(u64, u64(ptr(load(u64, offset(type_name, apy_str_ptr_offset())))),
          offset(o, apy_e_name_offset()))
    store(u64, u64(apy_none()), offset(o, apy_e_arg_offset()))
    return apy_exc_construct_of(o, ptr(0), 0)


def apy_exc_type(name: ptr) -> ptr:
    """The TYPE OBJECT an exception name answers, as a value.

    THE CLASS THE PROGRAM WROTE WINS, when it wrote one: `except AppError:`,
    `isinstance(e, AppError)` and `super()` inside its own method must all
    reach the same object, and the table is what makes them.

    OTHERWISE A CELL IS BUILT AND IMMEDIATELY ASKED FOR ITS TYPE, so what a
    program holding `ValueError` has is a plain type object -- not an
    exception. That distinction is what stops `e = ValueError("v"); e()` from
    reading as a second construction.
    """
    user: ptr = apy_exc_class_named_of(
        ptr(load(u64, offset(name, apy_str_ptr_offset()))))
    if user:
        return user
    o: ptr = apy_obj_alloc(apy_exc_kind())
    if not o:
        return o
    store(i64, -1, offset(o, apy_e_pos_offset()))
    store(u64, load(u64, offset(name, apy_str_ptr_offset())),
          offset(o, apy_e_name_offset()))
    store(u64, u64(apy_none()), offset(o, apy_e_arg_offset()))
    return apy_type_for(o)


def apy_raise_from(exc: ptr, cause: ptr, has_cause: i64) -> ptr:
    """`raise exc from cause`.

    `suppress` IS SET EVEN WHEN THERE IS NO CAUSE, because `raise X from None`
    is the spelling that HIDES a context -- the flag says whether to print
    the chain, not whether one exists.
    """
    if i64(load(i32, offset(exc, 0))) == apy_exc_kind():
        store(i32, i32(1), offset(exc, apy_e_suppress_offset()))
        keep: ptr = ptr(0)
        if has_cause:
            if i64(load(i32, offset(cause, 0))) == apy_exc_kind():
                keep = cause
        store(u64, u64(keep), offset(exc, apy_e_cause_offset()))
    return apy_raise(exc)


def apy_group_select_of(g: ptr, want: ptr, keep: i64) -> ptr:
    """PEP 654: the part of an exception group matching `want`, or null.

    NULL MEANS "NOTHING MATCHED" and is not an error, which is what lets
    `except*` decide whether a clause runs at all: an empty group would be
    caught by a clause that matched nothing.

    NESTED GROUPS ARE RECURSED INTO AND KEEP THEIR SHAPE. A group inside a
    group is selected from and the RESULT goes in as a group -- flattening
    would lose which raise the leaves came from, which is the structure the
    PEP exists to preserve.

    `keep` IS THE SIDE WANTED, because `split` needs both: the matching part
    and everything else, from one walk with the test inverted.

    THE MESSAGE COMES FROM THE ORIGINAL, so the piece reads like what it was
    taken from rather than like a new failure.
    """
    if i64(load(i32, offset(g, 0))) != apy_exc_kind():
        return ptr(0)
    subs: ptr = ptr(load(u64, offset(g, apy_e_subs_offset())))
    if not subs:
        return ptr(0)
    picked: ptr = apy_seq_new_of(apy_list_kind(), 4)
    if not picked:
        return ptr(0)
    n: i64 = load(i64, offset(subs, apy_q_n_offset()))
    items: ptr = ptr(load(u64, offset(subs, apy_q_items_offset())))
    i: i64 = 0
    while i < n:
        one: ptr = ptr(load(u64, offset(items, i * apy_value_size())))
        nested: i64 = 0
        if i64(load(i32, offset(one, 0))) == apy_exc_kind():
            if ptr(load(u64, offset(one, apy_e_subs_offset()))):
                nested = 1
        if nested:
            inner: ptr = apy_group_select_of(one, want, keep)
            if inner:
                apy_q_append_of(picked, inner)
        else:
            hit: ptr = apy_isinstance(one, want)
            if not hit:
                return ptr(0)
            side: i64 = 0
            if keep:
                side = 1
            got: i64 = 0
            if apy_truth(hit):
                got = 1
            if got == side:
                apy_q_append_of(picked, one)
        i = i + 1
    if load(i64, offset(picked, apy_q_n_offset())) == 0:
        return ptr(0)
    arg: ptr = apy_none()
    if load(i32, offset(g, apy_e_has_arg_offset())):
        arg = ptr(load(u64, offset(g, apy_e_arg_offset())))
    out: ptr = apy_make_exc(
        apy_from_cstr(rodata(b"ExceptionGroup\0")), arg)
    if not out:
        return ptr(0)
    store(u64, u64(picked), offset(out, apy_e_subs_offset()))
    return out


def apy_group_receiver(g: ptr, meth: ptr) -> i64:
    """Is `g` an exception group at all? Refuses by NAME if not.

    A PLAIN EXCEPTION HAS NO `split`, which is what the message says: the
    method belongs to a group, and an ordinary exception simply does not have
    the attribute.
    """
    if i64(load(i32, offset(g, 0))) == apy_exc_kind():
        if ptr(load(u64, offset(g, apy_e_subs_offset()))):
            return 1
    apy_raise_fmt(
        rodata(b"AttributeError\0"),
        rodata(b"'%s' object has no attribute '%s'\0"),
        apy_kind_name_of(g), meth)
    return 0


def apy_group_split(g: ptr, want: ptr) -> ptr:
    """`eg.split(T)` -- the matching part and everything else.

    BOTH HALVES COME FROM THE SAME WALK with the test inverted, and either
    may be None: a group where nothing matched splits into `(None, eg)`, and
    one where everything did into `(eg, None)`.
    """
    if not apy_group_receiver(g, rodata(b"split\0")):
        return ptr(0)
    hit: ptr = apy_group_select_of(g, want, 1)
    if apy_error_occurred():
        return ptr(0)
    miss: ptr = apy_group_select_of(g, want, 0)
    if apy_error_occurred():
        return ptr(0)
    out: ptr = apy_seq_new_of(apy_tuple_kind(), 2)
    if not out:
        return out
    if hit:
        apy_seq_push(out, hit)
    else:
        apy_seq_push(out, apy_none())
    if miss:
        apy_seq_push(out, miss)
    else:
        apy_seq_push(out, apy_none())
    return out


def apy_group_subgroup(g: ptr, want: ptr) -> ptr:
    """`eg.subgroup(T)` -- the matching part, or None."""
    if not apy_group_receiver(g, rodata(b"subgroup\0")):
        return ptr(0)
    got: ptr = apy_group_select_of(g, want, 1)
    if apy_error_occurred():
        return ptr(0)
    if got:
        return got
    return apy_none()


def apy_group_dispatch(raised: ptr, types: ptr) -> ptr:
    """`except*` -- which clause gets what, and what is left over.

    A PLAIN EXCEPTION IS WRAPPED so one walk serves both shapes: `except*
    ValueError` catches a bare ValueError, and the only way to say that with
    a group selector is to make it a group of one first.

    EACH CLAUSE TAKES ITS PART AND THE REST CARRIES ON, which is what makes
    `except*` clauses independent: the second clause sees what the first did
    not take.

    NOTHING MATCHED MEANS THE ORIGINAL PROPAGATES -- not the wrapper this
    made to split with. A program catching the plain ValueError outside has
    to see the ValueError.

    A WRAPPER WITH ONE LEFTOVER IS UNWRAPPED for the same reason: what
    escapes should be what was raised, not a group that was never written.
    """
    rest: ptr = ptr(0)
    wrapped: i64 = 0
    plain: i64 = 1
    if i64(load(i32, offset(raised, 0))) == apy_exc_kind():
        if ptr(load(u64, offset(raised, apy_e_subs_offset()))):
            plain = 0
    if plain:
        one: ptr = apy_seq_new_of(apy_list_kind(), 1)
        if not one:
            return ptr(0)
        apy_seq_push(one, raised)
        rest = apy_make_exc(apy_from_cstr(rodata(b"ExceptionGroup\0")),
                            apy_from_cstr(rodata(b"\0")))
        if not rest:
            return ptr(0)
        store(u64, u64(one), offset(rest, apy_e_subs_offset()))
        wrapped = 1
    else:
        rest = raised
    n: i64 = load(i64, offset(types, apy_q_n_offset()))
    items: ptr = ptr(load(u64, offset(types, apy_q_items_offset())))
    out: ptr = apy_seq_new_of(apy_tuple_kind(), n + 1)
    if not out:
        return out
    any_hit: i64 = 0
    i: i64 = 0
    while i < n:
        want: ptr = ptr(load(u64, offset(items, i * apy_value_size())))
        hit: ptr = ptr(0)
        if rest:
            hit = apy_group_select_of(rest, want, 1)
            if apy_error_occurred():
                return ptr(0)
        if hit:
            any_hit = 1
            rest = apy_group_select_of(rest, want, 0)
            if apy_error_occurred():
                return ptr(0)
            apy_seq_push(out, hit)
        else:
            apy_seq_push(out, apy_none())
        i = i + 1
    unwrap: i64 = 0
    if rest:
        if wrapped:
            unwrap = 1
    if not any_hit:
        rest = raised
    elif unwrap:
        left: ptr = ptr(load(u64, offset(rest, apy_e_subs_offset())))
        if load(i64, offset(left, apy_q_n_offset())) == 1:
            rest = ptr(load(u64, offset(
                ptr(load(u64, offset(left, apy_q_items_offset()))), 0)))
    if rest:
        apy_seq_push(out, rest)
    else:
        apy_seq_push(out, apy_none())
    return out
