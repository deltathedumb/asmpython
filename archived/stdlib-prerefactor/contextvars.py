"""Values that are local to a context rather than to a thread or a call.

There is ONE context here and no concurrency to be local to, so a `ContextVar`
is a cell with an undo stack: `set` hands back a token that remembers what was
there, and `reset` puts it back. That is exactly the observable behaviour of
the real thing in a program that never switches contexts, which is every
program this compiler can run today.

WHAT THAT DOES NOT COVER: a task that sets a var and expects a sibling task
not to see it. `copy_context().run(...)` here runs in the SAME values rather
than a snapshot of them, so a program relying on the isolation gets the
current values. Real isolation needs the runtime to own a context stack and
swap it at every await, which is a scheduler feature and not a library one.
"""

#: Marks "no default was given", which is different from a default of None.
_NO_DEFAULT = object()


class Token:
    """What `set` returns: enough to undo exactly that write."""

    MISSING = _NO_DEFAULT

    def __init__(self, var, old, existed):
        self.var = var
        self._old = old
        self._existed = existed
        # PLAIN ATTRIBUTES, not properties: `old_value` reads as MISSING when
        # the var had never been set, and that is a value to compute once
        # here rather than a descriptor to consult on every read.
        self.old_value = old if existed else Token.MISSING


class ContextVar:
    def __init__(self, name, default=_NO_DEFAULT):
        self._name = name
        self._default = default
        self._value = _NO_DEFAULT

    @property
    def name(self):
        return self._name

    def get(self, *default):
        if self._value is not _NO_DEFAULT:
            return self._value
        if default:
            return default[0]
        if self._default is not _NO_DEFAULT:
            return self._default
        raise LookupError(self._name)

    def set(self, value):
        token = Token(self, self._value, self._value is not _NO_DEFAULT)
        self._value = value
        return token

    def reset(self, token):
        if token.var is not self:
            raise ValueError("Token was created by a different ContextVar")
        # BACK TO UNSET, not to None: a var that had never been set reads its
        # default again after the reset, and storing None would shadow it.
        self._value = token._old if token._existed else _NO_DEFAULT

    def __repr__(self):
        return "<ContextVar name=" + repr(self._name) + ">"


class Context:
    def run(self, callable_, *args, **kwargs):
        return callable_(*args, **kwargs)

    def copy(self):
        return Context()

    def __iter__(self):
        return iter([])

    def __len__(self):
        return 0


def copy_context():
    return Context()
