"""Weak references, over the reference interpreter's real reference count.

COVERAGE: `ref` -- `ref(obj)`, `ref(obj, callback)`, calling it to get `obj`
back (or `None` once `obj` is gone); the callback fires at most once,
exactly when `obj`'s own last reference drops (`objects/host.py`'s
`ObjectHost._finalize`), and receives the `ref` OBJECT ITSELF as its one
argument, matching CPython's own documented convention -- not `obj`, which
is gone by the time anything could receive it. CALLBACK-FREE REFS ARE
INTERNED, as CPython interns them: `ref(o) is ref(o)` is True and
`getweakrefcount(o)` counts one, while a `ref` WITH a callback is always
its own object (two callbacks both have to run). `ref(obj) == ref(obj)`
(equal when both are alive and their referents are equal, or when both are
dead) and `hash(ref(obj))` (equal to `hash(obj)`, computed once and kept
so it still answers after `obj` is gone, exactly as CPython's does).
`getweakrefcount(obj)`, and the `TypeError` for a target that cannot be
weakly referenced at all.

TIMING IS THE SHADOW REFCOUNT'S, which is exact for the shapes
`docs/STDLIB.md` records as exact -- a name dropped by `del`, by
reassignment, or by its function returning -- and LATE, never early, for
the ones it records as gaps. A referent held only by a temporary that was
passed into a call is one of those: it dies when the enclosing frame ends
rather than at the statement. `r() is None` specifically is exact, since
`apy_is` is on the audited non-retaining list (`ir/interpreter.py`'s
`_NON_RETAINING`) -- which is the shape a program actually asks a weakref
about.

INTERPRETER-ONLY. `apy_weakref_register`/`apy_weakref_deref`
(`objects/host.py`) read and are read by the shadow reference count
this whole runtime's `__del__` timing is built on
(`docs/STDLIB.md`/`ir/interpreter.py`), which only the reference
interpreter keeps -- a compiled build has no such count and these two
calls refuse BY NAME there (`objects/c/_builtins.py`'s own comment says
why) rather than answer a plausible-looking wrong number.

NOT COVERED, each refused BY NAME rather than approximated:
`weakref.proxy` -- forwarding every possible operation (`+`, `[]`, every
dunder) through to a possibly-dead target, and raising `ReferenceError`
from each one instead of just `__call__`, is a different and much larger
surface than `ref` itself needs. `finalize` -- its `.detach()`/`.peek()`
and "runs even without an object, and at most once even if invoked both
manually and by collection" semantics are their own separate state
machine on top of `ref`, not a thin wrapper over it.
`WeakValueDictionary`/`WeakKeyDictionary`/`WeakSet` -- each needs its
entries PRUNED as their values/keys/members die, which means the
dictionary or set itself must be one of `_finalize`'s own callbacks for
every entry it holds; nothing here reaches back into a container's own
storage from a callback registered on one of its elements.
"""


class ReferenceError(Exception):
    pass


class ref:
    def __new__(cls, obj, callback=None):
        # CPython INTERNS CALLBACK-FREE WEAKREFS: `ref(o) is ref(o)` is
        # True, and `getweakrefcount(o)` counts one rather than two,
        # because the second call hands back the first object. Reproduced
        # rather than skipped, since both of those are things a program
        # can read. One WITH a callback is never shared -- two callbacks
        # both have to run.
        if callback is None:
            got = apy_weakref_existing(obj)
            if got is not None:
                return got
        return object.__new__(cls)

    def __init__(self, obj, callback=None):
        # THE TARGET IS NOT STORED HERE. `self._target = obj` would be an
        # ordinary attribute assignment, and an ordinary attribute holds a
        # STRONG reference (`objects_host._attr_store` increfs what it
        # stores, as it must for every other attribute in the language) --
        # which would keep every referent alive forever and defeat the
        # whole point. The handle goes into `ObjectHost._weakref_target`
        # instead, keyed by THIS object, where nothing counts it; see
        # `_apy_weakref_register`.
        #
        # THE HASH IS TAKEN NOW AND KEPT, never recomputed once `obj` is
        # gone -- exactly what CPython's own `ref.__hash__` does, since
        # `hash(a_dead_ref)` has nothing left to hash. `hash(obj)` here
        # still works fine: `obj` is alive for the whole constructor.
        self._hash = hash(obj)
        apy_weakref_register(obj, self, callback)

    def __call__(self):
        return apy_weakref_deref(self)

    def __eq__(self, other):
        if not isinstance(other, ref):
            return NotImplemented
        a, b = self(), other()
        if a is None and b is None:
            # BOTH DEAD is equal -- CPython's own rule, checked directly
            # against the installed interpreter rather than assumed.
            return True
        if a is None or b is None:
            return False
        return a == b

    def __hash__(self):
        return self._hash

    def __repr__(self):
        obj = self()
        if obj is None:
            return f"<weakref at {id(self)}; dead>"
        return f"<weakref at {id(self)}; to '{type(obj).__name__}' at {id(obj)}>"


def getweakrefcount(obj):
    """How many live `ref`s point at `obj` right now.

    NOT a field this module keeps up to date on its own -- a dead `ref`'s
    OWN slot in `ObjectHost._weakrefs[target]` is popped by `_finalize`
    only when `target` itself dies, not when the `ref` does, so a `ref`
    that went out of scope before its target counts as gone here the
    same way `apy_weakref_deref` would answer for it: skipped rather
    than counted.
    """
    return apy_weakref_count(obj)
