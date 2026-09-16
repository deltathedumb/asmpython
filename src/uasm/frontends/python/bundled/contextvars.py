"""Context-local state, as ordinary Python this compiler compiles.

COVERAGE: `ContextVar` -- construction with an optional keyword-only
`default=`, `.name`, `.get()` both with and without a call-time default
argument (which overrides the constructor's default for that one call), the
`LookupError` it raises when a variable has neither a value in the current
`Context` nor a constructor default, and `.set()` / `.reset(token)` as an
exact round trip. `Token` -- `.var`, `.old_value`, the `Token.MISSING`
sentinel a `.old_value` holds when a `set()` had nothing to restore, refusing
a token used twice (`RuntimeError`) and a token from a different `Context`
(`ValueError`). `Context` as a full read-only mapping over the `ContextVar`s
it holds a value for -- `__getitem__`, `__contains__`, `.get()`, `.keys()`,
`.values()`, `.items()`, `__len__`, `__iter__`, `__eq__` -- plus `.run(fn,
*args, **kwargs)`, which makes the context current for the call and restores
whatever was current before, and refuses to re-enter a `Context` already
running (`RuntimeError`). `Context()` builds an empty context with no
inherited values; `copy_context()` snapshots the values visible right now
into a new, independent `Context`.

NOT COVERED: automatic per-Task context isolation across `asyncio` task
boundaries. CPython's real `Task` calls `contextvars.copy_context()` the
moment it is created and runs every step of the wrapped coroutine inside
that copy (`Lib/asyncio/tasks.py`), which is WHY two tasks that `.set()` the
same `ContextVar` do not see each other's writes, and why the code that
created a task does not see what the task sets until the task finishes. This
module implements `ContextVar`, `Token`, `Context` and `copy_context()`
exactly to that specification for SEQUENTIAL code -- it is the same
machinery CPython's own `Task` is written in terms of -- but
`asyncio.create_task` here (`apy_asyncio_create_task`,
`src/uasm/runtime/tasks.py`) wraps a coroutine as "a generator with a
flag" and the scheduler steps it directly; nothing captures or restores a
`Context` around a step. So even a SINGLE coroutine handed to a bare
`asyncio.run(...)` already diverges here, with no concurrency involved at
all: CPython's `asyncio.run` wraps it in exactly one `Task`, so a
`ContextVar.set()` made inside is confined to that task's private copy and
is gone once `asyncio.run` returns, while a `ContextVar.set()` made BEFORE
is still visible inside (the copy inherits it) -- and uasm's version of
the same program leaks the inner `.set()` back out, because there is no
copy to confine it to. Checked, not assumed: a scratch probe of

    v.set("before-run")
    async def worker():
        print(v.get())       # "before-run" on both -- inherited, no copy needed yet
        v.set("inside-task")
        await asyncio.sleep(0)
    asyncio.run(worker())
    print(v.get())            # "before-run" on CPython, "inside-task" here

prints `before-run` twice under CPython and `before-run` then `inside-task`
under uasm. It is refused BY NAME rather than silently mismeasured:
`tests/stdlib/contextvars.py` has no `asyncio` in it at all, because there
is no shape of `.set()`-inside-a-task that this gap leaves matching CPython
to test.

Also not covered: iteration order. CPython's `Context` is backed by a hash
array mapped trie keyed by each `ContextVar`'s identity hash, so the order
`keys()`/`values()`/`items()`/`__iter__` visit entries in is not insertion
order and is not specified by the documentation either. This one is an
ordinary dict keyed by insertion order, which is simpler and equally
unspecified from the outside -- so nothing here is tested for a particular
order, only for membership and content, which is what CPython itself
guarantees.
"""

__all__ = ("ContextVar", "Context", "Token", "copy_context")


class _NoDefault:
    """The constructor's and `.get()`'s "nothing was given" sentinel.

    A DIFFERENT sentinel from `Token.MISSING`: a `ContextVar` with no
    constructor default and a `.get()` with no argument are both "not
    given", while `Token.MISSING` means "there really was no prior value",
    and the two must never be confused -- `default=Token.MISSING` would be a
    perfectly ordinary value to store.
    """

    __slots__ = ()

    def __repr__(self):
        return "<no default>"


_NO_DEFAULT = _NoDefault()


class _TokenMissingType:
    """The type of `Token.MISSING`.

    A MODULE-LEVEL CLASS, deliberately, not one nested inside `Token`: this
    compiler's bundled-module splice does not yet bind a `class` statement
    written directly inside another class's body back into that class's own
    namespace -- `Token.MISSING = _TokenMissingType()` right below a nested
    `class _TokenMissingType:` raised `NameError: name '_TokenMissingType'
    is not defined` at the assignment, though the identical two statements
    work as an ordinary top-level program and even as the exact text this
    splice produces, replayed outside the real import pipeline. So the fault
    is narrower than the splice's renaming pass -- confirmed by dumping its
    output and re-running it standalone, which worked -- and sits somewhere
    later in how a class body's own nested definitions are lowered. `dataclasses.MISSING`
    already set the precedent for a plain module-level sentinel type; this
    follows it rather than chasing a lowering bug a sentinel does not need.
    """

    __slots__ = ()

    def __repr__(self):
        return "<Token.MISSING>"


class Token:
    """What `ContextVar.set()` returns, and the only thing `.reset()` takes.

    Ties a value back to the exact `set()` call that produced it: which
    `ContextVar`, what to restore, and which `Context` it happened in --
    `.reset()` refuses a token used from any other `Context` or used twice.
    """

    #: The sentinel `.old_value` holds when the `set()` that made this token
    #: had no PRIOR value to restore -- so `.reset()` removes the variable
    #: from the context entirely rather than writing this sentinel into it.
    MISSING = _TokenMissingType()

    def __init__(self, var, old_value, context):
        self.var = var
        self.old_value = old_value
        self._context = context
        self._used = False

    def __repr__(self):
        state = "used " if self._used else ""
        return "<Token %svar=%r>" % (state, self.var)


class ContextVar:
    """A value that depends on which `Context` is current when it is read."""

    def __init__(self, name, *, default=_NO_DEFAULT):
        self._name = name
        self._default = default

    @property
    def name(self):
        return self._name

    def get(self, *default_arg):
        """The value in the CURRENT context.

        With no argument: the current context's value, else the
        constructor's `default=`, else `LookupError`. WITH one: that
        argument stands in for the constructor default for this call only,
        so a caller can supply a fallback even for a variable that was
        built with none.
        """
        if len(default_arg) > 1:
            raise TypeError(
                "get expected at most 1 argument, got %d" % len(default_arg))
        ctx = _current()
        if self in ctx._data:
            return ctx._data[self]
        if default_arg:
            return default_arg[0]
        if self._default is not _NO_DEFAULT:
            return self._default
        raise LookupError(self)

    def set(self, value):
        """Store `value` in the current context; return a `Token` to undo it."""
        ctx = _current()
        old = ctx._data[self] if self in ctx._data else Token.MISSING
        token = Token(self, old, ctx)
        ctx._data[self] = value
        return token

    def reset(self, token):
        """Undo exactly the `set()` that produced `token`."""
        if not isinstance(token, Token) or token.var is not self:
            raise ValueError("reset() must be called with a token that "
                             "belongs to this ContextVar")
        if token._used:
            raise RuntimeError("%r has already been used once" % (token,))
        if token._context is not _current():
            raise ValueError("%r was created in a different Context" % (token,))
        if token.old_value is Token.MISSING:
            if self in token._context._data:
                del token._context._data[self]
        else:
            token._context._data[self] = token.old_value
        token._used = True

    def __repr__(self):
        if self._default is _NO_DEFAULT:
            return "<ContextVar name=%r>" % (self._name,)
        return "<ContextVar name=%r default=%r>" % (self._name, self._default)


class Context:
    """A snapshot of every `ContextVar` value that has been set within it.

    A MAPPING from `ContextVar` to whatever it was last `.set()` to, here or
    inherited by `copy_context()` -- never by naming the variable, because
    the whole point is that reading `some_var.get()` finds it without a
    program having to know which context is current.
    """

    def __init__(self):
        self._data = {}
        self._entered = False

    def run(self, callable, *args, **kwargs):
        """Make this the CURRENT context for one call, then restore the old one.

        Whatever `callable` does with a `ContextVar` -- get, set, reset --
        lands in THIS context's data and nowhere else, which is what lets a
        caller run something and then discard everything it touched simply
        by not keeping the result.
        """
        if self._entered:
            raise RuntimeError(
                "cannot enter context: %r is already entered" % (self,))
        self._entered = True
        _push(self)
        try:
            return callable(*args, **kwargs)
        finally:
            _pop()
            self._entered = False

    def copy(self):
        """An independent `Context` starting from the same values."""
        made = Context()
        made._data = dict(self._data)
        return made

    def __getitem__(self, var):
        return self._data[var]

    def get(self, var, default=None):
        return self._data[var] if var in self._data else default

    def __contains__(self, var):
        return var in self._data

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(list(self._data))

    def keys(self):
        return list(self._data)

    def values(self):
        return [self._data[key] for key in self._data]

    def items(self):
        return [(key, self._data[key]) for key in self._data]

    def __eq__(self, other):
        if not isinstance(other, Context):
            return NotImplemented
        return self._data == other._data

    def __repr__(self):
        return "<Context object>"


#: THE AMBIENT CONTEXT. A program that never mentions `Context` at all still
#: has one -- every `ContextVar.get()`/`.set()` before any `.run()` reads and
#: writes this one, which is what makes plain module-level use of a
#: `ContextVar` behave exactly like a global variable until something
#: actually asks for isolation.
_root_context = Context()

#: THE STACK OF CURRENTLY-ENTERED CONTEXTS, innermost last. `Context.run()`
#: pushes and pops it; nothing else touches it. A stack rather than a single
#: slot because `.run()` calls nest -- one context's callable can call
#: `.run()` on a different context, and the inner one must give way to the
#: outer when it returns.
_context_stack = [_root_context]


def _current():
    return _context_stack[-1]


def _push(context):
    _context_stack.append(context)


def _pop():
    _context_stack.pop()


def copy_context():
    """A new `Context` holding every value visible right now.

    INDEPENDENT from this point on: `copy_context()` then a `.set()` on the
    original does not change the copy, and running the copy does not change
    the original -- see `Context.run`.
    """
    return _current().copy()
