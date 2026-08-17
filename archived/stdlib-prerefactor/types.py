"""Runtime type objects.

Only `ModuleType` and the small aliases, because those are the parts of
`types` that are OBJECTS a program builds rather than descriptions of the
interpreter's own internals. `FunctionType`, `CodeType` and friends name
things this runtime represents differently, and an alias to something else
would be a wrong answer rather than a missing one.
"""


class ModuleType:
    """A namespace object. `m.x = 1` then `m.x` -- that is all a module is to
    a program that builds one by hand.

    `__getattr__` ON THE INSTANCE is honoured for the same reason PEP 562 gave
    it to real modules: a module's fallback is a module-level function, not a
    method on a class nobody wrote.
    """

    def __init__(self, name, doc=None):
        self.__name__ = name
        self.__doc__ = doc

    def __repr__(self):
        return "<module " + repr(self.__name__) + ">"


# THE CLASS IS CALLED `module`, which is what `type(m).__name__` answers in
# CPython -- the name `ModuleType` is the one the module exports it under and
# not the one the type carries.
ModuleType.__name__ = "module"
ModuleType.__qualname__ = "module"


class SimpleNamespace:
    """Attributes and nothing else, with a repr that shows them."""

    def __init__(self, **kwargs):
        for key in kwargs:
            setattr(self, key, kwargs[key])

    def __repr__(self):
        # IN THE ORDER THEY WERE SET, which is what CPython shows -- sorting
        # them would make `namespace(b=2, a=1)` print as `namespace(a=1, b=2)`
        # and lose the only thing the repr says about how it was built.
        parts = []
        held = self.__dict__
        for key in held:
            parts.append(key + "=" + repr(held[key]))
        return "namespace(" + ", ".join(parts) + ")"

    def __eq__(self, other):
        if isinstance(other, SimpleNamespace):
            return self.__dict__ == other.__dict__
        return NotImplemented

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got


def new_class(name, bases=(), kwds=None, exec_body=None):
    """`type(name, bases, ns)` with the body filled in by a callback."""
    ns = {}
    if exec_body is not None:
        exec_body(ns)
    return type(name, bases, ns)
