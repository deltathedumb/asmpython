"""Shallow and deep copying.

COVERAGE: `copy`, `deepcopy`, the `__copy__` and `__deepcopy__` hooks, the memo
and the shared/self-referential structures it exists for, and `Error`/`error`.

NOT COVERED: `__reduce__` and `__reduce_ex__`, `__getstate__`/`__setstate__`,
`copyreg` dispatch, `deepcopy` of a class or a function (both are returned
unchanged, as CPython does), and `replace` (3.13's `copy.replace`).

`Error` IS NOT USABLE WITH `issubclass` YET, and that is the compiler rather
than this module. A bundled module's exception class is spliced under a mangled
name and the mangling leaks: `issubclass(copy.Error, Exception)` answers False,
and a raised one reports `type(e).__name__` as `_asmpy_bundled_copy_Error`.
Catching it as `Exception` or by its own name works. See `docs/STDLIB.md`.

The difference is one question: does the copy share the objects the original
pointed at, or does it get copies of those too? `copy` shares them and
`deepcopy` does not, and every subtlety below is about the second one --
because "copy everything reachable" has to cope with a graph that contains
itself.

THE MEMO IS NOT AN OPTIMISATION. An object reachable twice must come out as
ONE object in the copy, or a structure that shared a node stops sharing it;
and an object reachable from itself would recurse forever without it.
"""


def copy(x):
    """A new object whose contents are the SAME objects the original held."""
    hook = getattr(x, "__copy__", None)
    if hook is not None:
        return hook()
    if _is_atomic(x):
        return x
    if isinstance(x, list):
        return x[:]
    if isinstance(x, dict):
        return dict(x)
    if isinstance(x, set):
        return set(x)
    if isinstance(x, bytearray):
        return bytearray(x)
    if isinstance(x, (tuple, frozenset)):
        # ALREADY IMMUTABLE: nothing can write through the copy, so the object
        # itself is a correct copy of itself. CPython answers the same object.
        return x
    made = _reconstruct(x)
    if made is not None:
        return made
    return x


def deepcopy(x, memo=None):
    """A new object holding COPIES of everything the original could reach."""
    if memo is None:
        memo = {}
    hook = getattr(x, "__deepcopy__", None)
    if hook is not None:
        return hook(memo)
    if _is_atomic(x):
        return x
    key = id(x)
    if key in memo:
        return memo[key]
    if isinstance(x, list):
        made = []
        # PUT IN THE MEMO BEFORE THE CONTENTS ARE COPIED, so a list that
        # contains itself finds the (still empty) copy rather than recursing.
        memo[key] = made
        for item in x:
            made.append(deepcopy(item, memo))
        return made
    if isinstance(x, dict):
        made = {}
        memo[key] = made
        for k in x:
            made[deepcopy(k, memo)] = deepcopy(x[k], memo)
        return made
    if isinstance(x, tuple):
        made = tuple([deepcopy(item, memo) for item in x])
        memo[key] = made
        return made
    if isinstance(x, set):
        made = set()
        memo[key] = made
        for item in x:
            made.add(deepcopy(item, memo))
        return made
    if isinstance(x, frozenset):
        made = frozenset([deepcopy(item, memo) for item in x])
        memo[key] = made
        return made
    if isinstance(x, bytearray):
        made = bytearray(x)
        memo[key] = made
        return made
    made = _reconstruct(x, memo)
    if made is not None:
        memo[key] = made
        return made
    return x


def _is_atomic(x):
    """Has this nothing inside it to copy? Then it IS its own copy."""
    if x is None or x is Ellipsis or x is NotImplemented:
        return True
    return isinstance(x, (int, float, bool, complex, str, bytes, type))


def _reconstruct(x, memo=None):
    """A user object copied by rebuilding it around a copy of its `__dict__`.

    WITHOUT RUNNING `__init__`: the state is what is being copied, and an
    `__init__` would compute a different one from arguments nobody kept.
    """
    state = getattr(x, "__dict__", None)
    if state is None:
        return None
    made = object.__new__(type(x))
    if memo is not None:
        memo[id(x)] = made
    for key in state:
        value = state[key]
        # `object.__setattr__` AND NOT `setattr`. A class that refuses writes
        # -- a frozen dataclass is the one every program has -- raises from its
        # own `__setattr__` when a copy is being rebuilt, which is absurd: the
        # copy is not being MUTATED, it is being constructed. CPython restores
        # state through `__dict__` for the same reason and never consults the
        # class's `__setattr__` either.
        object.__setattr__(made, key,
                           deepcopy(value, memo) if memo is not None else value)
    return made


class Error(Exception):
    """What `copy` raises for something it cannot copy."""


error = Error
