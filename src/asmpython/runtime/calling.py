# Calling a Python function, in the machine subset.
#
# STAGE 6 OF docs/INERT-RUNTIME.md, and the last wall in the port. Thirteen
# exported functions were blocked on this one and nothing else: `apy_setattr`,
# `apy_getiter`, `apy_enter`/`apy_exit`, `apy_all`/`apy_any`, the whole `with`
# and `async with` machinery. Every one of them calls back into Python code,
# and until this had an IR spelling none of them could have one.
#
# ── why this is a SPLIT and not a replacement ──────────────────────────────
#
# `apy_call_nk` underneath is a hundred and twenty-five lines and almost all
# of it is argument MATCHING: a metaclass `__call__`, an exception class used
# as a value, instantiation, a callable instance, a bound receiver, `*rest`,
# `**kw`, keyword-only parameters, and defaults filling the tail. None of that
# is the shape a program's own function call takes.
#
# The case a program writes is: a plain compiled function, called with exactly
# as many arguments as it declares. That is what this answers. Everything else
# goes to `apy_call_slow` -- which is the same C that always ran, reached the
# one way a ported function is allowed to reach C at all.
#
# A REPLACEMENT WAS TRIED AND DOES NOT WORK. Promoting `apy_call_nk` to an
# export and porting the two-line wrapper over it leaves the ported half
# CALLING a C function, which is exactly what the closure invariant forbids --
# the whole point of the port is that a backend owes three functions, and a
# runtime that calls `apy_call_nk_of` owes four. Promoting a static works when
# IR implements the body. It does not work when IR merely calls it.
#
# ── the dispatch ───────────────────────────────────────────────────────────
#
# SEVENTEEN SPELLINGS OF ONE CALL, because the subset has no varargs and a
# function pointer has to be called with the exact signature it was compiled
# with. The C writes the same seventeen as a `switch`; here they are `if`s,
# and `callptr` is the intrinsic that finally made an indirect call sayable.
#
# The count is the C's: a function of more than sixteen parameters is refused
# there, and a program that writes one gets that refusal from the slow half
# rather than a wrong answer from here.


def apy_fn_kwonly_count(f: ptr) -> i64:
    """How many of a function's parameters are keyword-only."""
    return i64(load(i32, offset(f, apy_fn_kwonly_offset())))


def apy_call_plain(f: ptr, argc: i64) -> i64:
    """Is this a call the fast path can make?

    EVERY TEST HERE IS A SHAPE THE MATCHER WOULD HAVE HAD TO HANDLE, and each
    is written out rather than folded together so that adding one later is a
    line and not a rearrangement.

    ARITY MATCHING EXACTLY IS WHAT MAKES THE DEFAULTS IRRELEVANT: the C fills
    the tail from `defaults` only when the caller supplied fewer arguments
    than the function declares, so a function WITH defaults still takes this
    path when they are all supplied. It is the mismatch that is slow, not the
    declaration.

    A NATIVE HAS NO CODE POINTER -- the selector is the whole of it -- so it
    is refused first, the same order `apy_invoke` uses and for the same
    reason: calling through a null entry is the crash this arrangement
    replaced.
    """
    if i64(load(i32, offset(f, 0))) != apy_func_kind():
        return 0
    if load(i32, offset(f, apy_fn_native_offset())) != 0:
        return 0
    if load(u64, offset(f, apy_fn_bound_offset())) != 0:
        return 0
    if load(i32, offset(f, apy_fn_vararg_offset())) != 0:
        return 0
    if load(i32, offset(f, apy_fn_kwarg_offset())) != 0:
        return 0
    if apy_fn_kwonly_count(f) != 0:
        return 0
    if load(i64, offset(f, apy_fn_arity_offset())) != argc:
        return 0
    if load(u64, offset(f, apy_fn_code_offset())) == 0:
        return 0
    if argc < 0 or argc > 16:
        return 0
    return 1


def apy_call(f: ptr, argv: ptr, argc: i64) -> ptr:
    """Call `f` with `argc` arguments read from `argv`.

    `argv` IS THE ADDRESS OF AN ARRAY, not a value: the frontend puts the
    arguments in a stack slot and hands over its address, which is the same
    shape `apy_print` takes and for the same reason -- the IR has no varargs.
    """
    if not apy_call_plain(f, argc):
        return apy_call_slow(f, argv, argc)
    code: ptr = ptr(load(u64, offset(f, apy_fn_code_offset())))
    if argc == 0:
        return callptr(ptr, code, f)
    a0: ptr = ptr(load(u64, argv))
    if argc == 1:
        return callptr(ptr, code, f, a0)
    a1: ptr = ptr(load(u64, offset(argv, apy_value_size())))
    if argc == 2:
        return callptr(ptr, code, f, a0, a1)
    a2: ptr = ptr(load(u64, offset(argv, 2 * apy_value_size())))
    if argc == 3:
        return callptr(ptr, code, f, a0, a1, a2)
    a3: ptr = ptr(load(u64, offset(argv, 3 * apy_value_size())))
    if argc == 4:
        return callptr(ptr, code, f, a0, a1, a2, a3)
    a4: ptr = ptr(load(u64, offset(argv, 4 * apy_value_size())))
    if argc == 5:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4)
    a5: ptr = ptr(load(u64, offset(argv, 5 * apy_value_size())))
    if argc == 6:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5)
    a6: ptr = ptr(load(u64, offset(argv, 6 * apy_value_size())))
    if argc == 7:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6)
    a7: ptr = ptr(load(u64, offset(argv, 7 * apy_value_size())))
    if argc == 8:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7)
    a8: ptr = ptr(load(u64, offset(argv, 8 * apy_value_size())))
    if argc == 9:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8)
    a9: ptr = ptr(load(u64, offset(argv, 9 * apy_value_size())))
    if argc == 10:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9)
    a10: ptr = ptr(load(u64, offset(argv, 10 * apy_value_size())))
    if argc == 11:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9,
                       a10)
    a11: ptr = ptr(load(u64, offset(argv, 11 * apy_value_size())))
    if argc == 12:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9,
                       a10, a11)
    a12: ptr = ptr(load(u64, offset(argv, 12 * apy_value_size())))
    if argc == 13:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9,
                       a10, a11, a12)
    a13: ptr = ptr(load(u64, offset(argv, 13 * apy_value_size())))
    if argc == 14:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9,
                       a10, a11, a12, a13)
    a14: ptr = ptr(load(u64, offset(argv, 14 * apy_value_size())))
    if argc == 15:
        return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9,
                       a10, a11, a12, a13, a14)
    a15: ptr = ptr(load(u64, offset(argv, 15 * apy_value_size())))
    return callptr(ptr, code, f, a0, a1, a2, a3, a4, a5, a6, a7, a8, a9,
                   a10, a11, a12, a13, a14, a15)


# -- what the calling split made sayable -----------------------------------
#
# EVERY ONE OF THESE IS A CALL BACK INTO PYTHON CODE, which is why none of
# them could be written before `apy_call_n` had an IR half. They are small;
# the wall was never their size.
#
# `alloca` IS FINALLY USED HERE, and it has to be: `__exit__` may itself
# contain a `with`, and a reserved scratch would be overwritten by the inner
# one while the outer still held a pointer into it. A reserved word is right
# for a value read straight back out (`apy_split_limit`); an argument array
# outlives the call that fills it.


def apy_cm_missing(cm: ptr, which: ptr) -> ptr:
    """`with 5:` -- the object is not a context manager.

    THE MESSAGE NAMES WHICH METHOD WAS MISSED, which CPython does too, and it
    is worth keeping: a class that wrote `__enter__` and forgot `__exit__`
    reads as "not a context manager" without it.
    """
    return apy_raise_fmt(
        rodata(b"TypeError\0"),
        rodata(b"'%s' object does not support the context manager "
               b"protocol (missed %s method)\0"),
        apy_kind_name_of(cm), which)


def apy_enter(cm: ptr) -> ptr:
    """`with cm:` -- the `__enter__` half.

    `__exit__` IS LOOKED FOR FIRST, which is CPython's order and shows in the
    message: `with 5:` reports the missing `__exit__` rather than the missing
    `__enter__`, even though both are absent.

    THE ERROR IS CLEARED BETWEEN THE TWO LOOKUPS, because a failed `__exit__`
    lookup leaves one pending and the report built below would otherwise be
    the second error raised while the first still stood.
    """
    if not apy_dunder_of(cm, rodata(b"__exit__\0")):
        apy_error_clear()
        return apy_cm_missing(cm, rodata(b"__exit__\0"))
    m: ptr = apy_dunder_of(cm, rodata(b"__enter__\0"))
    if not m:
        apy_error_clear()
        return apy_cm_missing(cm, rodata(b"__enter__\0"))
    return apy_call(m, ptr(0), 0)


def apy_exit(cm: ptr, exc: ptr) -> ptr:
    """`__exit__(type, value, traceback)`. `exc` is the live exception or None.

    ALL THREE ARGUMENTS COME FROM THE ONE VALUE: the TYPE is what
    `et.__name__` reads, the VALUE is the exception itself, and the traceback
    is None because there are none here. Passing None for the type when there
    IS an exception would make `et.__name__` fail in a manager that logs it.
    """
    m: ptr = apy_dunder_of(cm, rodata(b"__exit__\0"))
    if not m:
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object does not support the context "
                   b"manager protocol%s\0"),
            apy_kind_name_of(cm), rodata(b"\0"))
    # A LITERAL, because `alloca` needs a size the compiler can work out
    # and a call is not one. Three values; the probe pins
    # `sizeof(apy_value)` to what `apy_value_size` returns, so the two
    # cannot drift apart without a test saying so.
    argv: ptr = alloca(24)
    if i64(load(i32, offset(exc, 0))) == apy_exc_kind():
        store(u64, u64(apy_type_for(exc)), argv)
        store(u64, u64(exc), offset(argv, apy_value_size()))
    else:
        store(u64, u64(apy_none()), argv)
        store(u64, u64(apy_none()), offset(argv, apy_value_size()))
    store(u64, u64(apy_none()), offset(argv, 2 * apy_value_size()))
    return apy_call(m, argv, 3)


def apy_aenter(cm: ptr) -> ptr:
    """`async with cm:` -- the `__aenter__` half.

    NO TWO-STEP LOOKUP HERE, unlike `apy_enter`: the async protocol has no
    ordering rule to reproduce, so the one missing method is the one reported.
    """
    m: ptr = apy_dunder_of(cm, rodata(b"__aenter__\0"))
    if not m:
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object does not support the asynchronous "
                   b"context manager protocol%s\0"),
            apy_kind_name_of(cm), rodata(b"\0"))
    return apy_call(m, ptr(0), 0)


def apy_unary_dunder_of(v: ptr, name: ptr) -> ptr:
    """`v.__name__()`, or 0 when the class does not define one.

    ZERO MEANS TWO THINGS and the caller tells them apart by the error flag:
    "no such method" and "it ran and failed". Every operator dispatch in this
    runtime reads it that way, which is why there is no out-parameter.
    """
    m: ptr = apy_dunder_of(v, name)
    if not m:
        return ptr(0)
    return apy_call(m, ptr(0), 0)


def apy_method1_of(v: ptr, name: ptr, arg: ptr) -> ptr:
    """`v.__name__(arg)`, or 0 when the class does not define one."""
    m: ptr = apy_dunder_of(v, name)
    if not m:
        return ptr(0)
    argv: ptr = alloca(8)     # one value; see `apy_exit`
    store(u64, u64(arg), argv)
    return apy_call(m, argv, 1)


def apy_aiter(src: ptr) -> ptr:
    """`async for x in src` -- the iterator the loop will step.

    AN ASYNC GENERATOR IS ALREADY ONE, and comes back untouched: `__aiter__`
    on one answers itself, so calling it would be a round trip to the same
    object.

    ANYTHING ELSE IS WRAPPED, because the loop steps its iterator by resuming
    a generator and an object with only `__anext__` is not one. The wrapper is
    a two-slot generator holding what to ask and where it got to -- which is
    what `APY_CORO_ANEXT` names.
    """
    if i64(load(i32, offset(src, 0))) == apy_gen_kind():
        if load(i32, offset(src, apy_g_agen_offset())) != 0:
            return src
    it: ptr = apy_dunder_of(src, rodata(b"__aiter__\0"))
    if not it:
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'async for' requires an object "
                   b"with __aiter__ method, got %s%s\0"),
            apy_kind_name_of(src), rodata(b"\0"))
    it = apy_call(it, ptr(0), 0)
    if not it:
        return ptr(0)
    if i64(load(i32, offset(it, 0))) == apy_gen_kind():
        if load(i32, offset(it, apy_g_agen_offset())) != 0:
            return it
    if not apy_dunder_of(it, rodata(b"__anext__\0")):
        apy_error_clear()
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'async for' requires an object "
                   b"with __aiter__ method, got %s%s\0"),
            apy_kind_name_of(it), rodata(b"\0"))
    wrap: ptr = apy_gen_new(ptr(0), 2)
    if not wrap:
        return wrap
    store(i32, i32(1), offset(wrap, apy_g_agen_offset()))
    store(i32, i32(apy_coro_anext()), offset(wrap, apy_g_builtin_offset()))
    slots: ptr = ptr(load(u64, offset(wrap, apy_g_slots_offset())))
    store(u64, u64(it), slots)
    store(u64, u64(0), offset(slots, apy_value_size()))
    return wrap


def apy_prepare(meta: ptr, name: ptr, bases: ptr) -> ptr:
    """`__prepare__` -- the mapping a class body is executed into.

    PEP 3115. A metaclass may answer an ordered or recording dict, which is
    how `enum` knows the order members were written in; without one the body
    fills a plain dict and nothing is lost.

    A `classmethod` IS WHAT `__prepare__` USUALLY IS, and it is reached
    through its own getter with the metaclass in front -- which is why the
    three-argument shape exists here at all. A plain function gets the two the
    protocol names.
    """
    if not meta:
        return apy_dict_new(8)
    if i64(load(i32, offset(meta, 0))) != apy_type_kind():
        return apy_dict_new(8)
    hook: ptr = apy_class_find_of(meta, apy_name_of(rodata(b"__prepare__\0")))
    if not hook:
        return apy_dict_new(8)
    got: ptr = ptr(0)
    if i64(load(i32, offset(hook, 0))) == apy_prop_kind():
        getter: ptr = ptr(load(u64, offset(hook, apy_prop_get_offset())))
        if getter:
            three: ptr = alloca(24)
            store(u64, u64(meta), three)
            store(u64, u64(name), offset(three, apy_value_size()))
            store(u64, u64(bases), offset(three, 2 * apy_value_size()))
            got = apy_call(getter, three, 3)
        else:
            two: ptr = alloca(16)
            store(u64, u64(name), two)
            store(u64, u64(bases), offset(two, apy_value_size()))
            got = apy_call(hook, two, 2)
    else:
        args: ptr = alloca(16)
        store(u64, u64(name), args)
        store(u64, u64(bases), offset(args, apy_value_size()))
        got = apy_call(hook, args, 2)
    if not got:
        return ptr(0)
    if i64(load(i32, offset(got, 0))) != apy_dict_kind():
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"__prepare__() must return a mapping%s%s\0"),
            rodata(b"\0"), rodata(b"\0"))
    return got


def apy_set_names(cls: ptr) -> ptr:
    """`__set_name__` on every descriptor the class body bound.

    PEP 487. A descriptor is told the class it landed in and the name it
    landed under, at the moment the class is finished -- which is how a
    `Field()` written once knows it is called `x`.

    ONLY AN INSTANCE CAN HAVE ONE, because the hook is looked for on the
    member\'s CLASS: a plain function or an int bound in the body is not a
    descriptor and is skipped rather than asked.
    """
    if i64(load(i32, offset(cls, 0))) != apy_type_kind():
        return apy_none()
    d: ptr = ptr(load(u64, offset(cls, apy_t_dict_offset())))
    n: i64 = load(i64, offset(d, apy_d_n_offset()))
    keys: ptr = ptr(load(u64, offset(d, apy_d_keys_offset())))
    vals: ptr = ptr(load(u64, offset(d, apy_d_vals_offset())))
    i: i64 = 0
    while i < n:
        member: ptr = ptr(load(u64, offset(vals, i * apy_value_size())))
        if i64(load(i32, offset(member, 0))) == apy_inst_kind():
            hook: ptr = apy_class_find_of(
                ptr(load(u64, offset(member, apy_o_cls_offset()))),
                apy_name_of(rodata(b"__set_name__\0")))
            if hook:
                args: ptr = alloca(16)
                store(u64, u64(cls), args)
                store(u64, load(u64, offset(keys, i * apy_value_size())),
                      offset(args, apy_value_size()))
                if not apy_call(apy_bind_of(hook, member), args, 2):
                    return ptr(0)
        i = i + 1
    return apy_none()


def apy_aexit(cm: ptr, exc: ptr) -> ptr:
    """`async with` -- the `__aexit__` half.

    ALL THREE ARGUMENTS FROM THE ONE VALUE, as `apy_exit` does it: the TYPE
    is what `et.__name__` reads, the VALUE is the exception, and the
    traceback is None because there are none here.

    `apy_exc_type` IS HANDED THE EXCEPTION AND NOT ITS NAME, which reads
    wrong and is what the C does: an exception cell keeps its name at the
    same offset a str keeps its bytes, so reading one as the other lands on
    exactly the name this wants. Reproduced rather than corrected, because a
    port that quietly changes an answer is worse than one that carries a
    surprise forward -- and the two halves have to agree.
    """
    m: ptr = apy_dunder_of(cm, rodata(b"__aexit__\0"))
    if not m:
        return apy_raise_fmt(
            rodata(b"TypeError\0"),
            rodata(b"'%s' object does not support the asynchronous "
                   b"context manager protocol%s\0"),
            apy_kind_name_of(cm), rodata(b"\0"))
    argv: ptr = alloca(24)
    if i64(load(i32, offset(exc, 0))) == apy_exc_kind():
        store(u64, u64(apy_exc_type(exc)), argv)
    else:
        store(u64, u64(apy_none()), argv)
    store(u64, u64(exc), offset(argv, apy_value_size()))
    store(u64, u64(apy_none()), offset(argv, 2 * apy_value_size()))
    return apy_call(m, argv, 3)


def apy_call_spread(f: ptr, args: ptr) -> ptr:
    """`f(*args)` -- call with a sequence spread into positions.

    THE ITEMS ARE COPIED rather than pointed at, which the C does too: the
    callee may hold the tuple and mutate a list it came from, and a call
    reading from live storage would then see arguments change under it.
    """
    n: i64 = load(i64, offset(args, apy_q_n_offset()))
    room: i64 = n
    if room == 0:
        room = 1
    argv: ptr = apy_alloc_bytes(room * apy_value_size())
    if not argv:
        return argv
    items: ptr = ptr(load(u64, offset(args, apy_q_items_offset())))
    i: i64 = 0
    while i < n:
        store(u64, load(u64, offset(items, i * apy_value_size())),
              offset(argv, i * apy_value_size()))
        i = i + 1
    return apy_call(f, argv, n)
