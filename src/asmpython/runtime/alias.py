# Parameterised types, and the flag that says which builtin a class extends.
#
# STAGE 5 OF docs/INERT-RUNTIME.md. `list[int]` and `dict[str, int]` are
# values a program can hold, print and pass to an annotation -- and NOTHING
# ELSE. An alias is not a type here: nothing is instantiated from one, no
# check consults it, and `isinstance(x, list[int])` is a TypeError in CPython
# too. So the whole kind is two fields kept so the repr can be rebuilt
# exactly, which is why it ports in one sitting.
#
# WHAT `apy_get_origin` AND `apy_get_args` ARE FOR: `typing.get_origin` and
# `typing.get_args`, which is the only way a program is expected to look
# inside one. Both answer something harmless when handed anything else --
# `None` and an empty tuple -- because that is what CPython does rather than
# raising, and a runtime that raised here would break every library that
# probes an annotation defensively.


def apy_alias_kind() -> i64:
    return 24


def apy_type_kind() -> i64:
    return 13


def apy_ga_origin_offset() -> i64:
    return 8


def apy_ga_args_offset() -> i64:
    return 16


def apy_t_builtin_offset() -> i64:
    return 56


def apy_alias_new(origin: ptr, args: ptr) -> ptr:
    """`origin[args]` as a value. Both halves kept, nothing derived."""
    cell: ptr = apy_obj_alloc(apy_alias_kind())
    if not cell:
        return cell
    store(u64, u64(origin), offset(cell, apy_ga_origin_offset()))
    store(u64, u64(args), offset(cell, apy_ga_args_offset()))
    return cell


def apy_get_origin(v: ptr) -> ptr:
    """`typing.get_origin(v)` -- `list` from `list[int]`, else None.

    NONE RATHER THAN AN ERROR for anything that is not an alias, because
    CPython answers None and every library that probes an annotation relies
    on it: `get_origin(int)` is None, not a TypeError.
    """
    if i64(load(i32, offset(v, 0))) == apy_alias_kind():
        return ptr(load(u64, offset(v, apy_ga_origin_offset())))
    return apy_none()


def apy_get_args(v: ptr) -> ptr:
    """`typing.get_args(v)` -- `(int,)` from `list[int]`, else empty.

    AN EMPTY TUPLE, not None, and the asymmetry with `get_origin` is
    CPython's rather than this runtime's: one answers "there is no origin"
    and the other "there are no arguments", and a caller unpacking the result
    would break on None.
    """
    if i64(load(i32, offset(v, 0))) == apy_alias_kind():
        return ptr(load(u64, offset(v, apy_ga_args_offset())))
    return apy_tuple_new(1)


def apy_type_builtin(cls: ptr, kind: i64) -> ptr:
    """Record which builtin kind a class EXTENDS, so `class C(dict)` works.

    A KIND NUMBER ON THE CLASS, which is how an instance of a subclass of a
    builtin is recognised as that builtin everywhere the runtime asks. Zero
    means it extends nothing -- an ordinary class -- so the field being
    zeroed by the allocator is already the right default.

    UNGUARDED, as the C is: the only caller is the class builder, which has
    just made the cell it is writing into.
    """
    store(i32, i32(kind), offset(cls, apy_t_builtin_offset()))
    return cls
