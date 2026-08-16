"""`warnings`, as ordinary Python this compiler compiles.

A COMPILED PROGRAM HAS NO WARNING FILTERS TO INHERIT and no interpreter state
to consult, so this is the whole mechanism rather than a view onto one: a
current action, a place to record what was raised, and a context manager that
saves and restores both. That is what the documented behaviour reduces to once
there is no runtime to ask.

WHAT IT DOES NOT DO: the default filters. CPython shows a `DeprecationWarning`
only in `__main__` and shows each warning once per location; both of those are
about a registry keyed by source position, which a compiled program does not
carry. `simplefilter` sets the action outright and that is the only control
here -- stated rather than approximated, because a filter that silently did
something else would be worse than one that is absent.
"""


class _Recorded:
    """One warning, as `catch_warnings(record=True)` hands it back."""

    def __init__(self, message, category):
        self.message = message
        self.category = category

    def __repr__(self):
        return "<warning " + self.category.__name__ + ">"


#: The current action, in a one-element list so a nested `catch_warnings` can
#: save and restore it without `global` -- which the compiler supports, but a
#: cell reads better here and matches how `_log` is shared.
_action = ["always"]

#: Where a warning goes while something is recording, or None.
_log = [None]


def simplefilter(action, *rest):
    """Set the action for every warning. `"ignore"` drops them."""
    _action[0] = action


def filterwarnings(action, *rest):
    """The narrow form, without the narrowing: this has no registry to key a
    filter on, so it sets the action outright as `simplefilter` does."""
    _action[0] = action


def warn(message, *rest, stacklevel=1):
    """Raise a warning through whatever is recording.

    `stacklevel` IS ACCEPTED AND UNUSED. It says how many frames up to blame,
    and there is no frame stack here to count -- but every caller in the
    standard library passes it, and a signature that refused it would turn an
    ordinary `warnings.warn(msg, cls, stacklevel=2)` into a TypeError about a
    keyword Python has always taken.
    """
    category = rest[0] if rest else UserWarning
    if _action[0] == "ignore":
        return
    if _action[0] == "error":
        raise category(message)
    if _log[0] is not None:
        _log[0].append(_Recorded(message, category))


class catch_warnings:
    """`with catch_warnings(record=True) as caught:` -- collect, then restore.

    The saved action is restored on the way out whether or not the block
    raised, which is what makes this usable around code that is expected to
    fail.
    """

    def __init__(self, **kw):
        self.record = kw.get("record", False)
        self.saved = None
        self.saved_log = None

    def __enter__(self):
        self.saved = _action[0]
        self.saved_log = _log[0]
        if self.record:
            _log[0] = []
            return _log[0]
        _log[0] = None
        return None

    def __exit__(self, kind, value, traceback):
        _action[0] = self.saved
        _log[0] = self.saved_log
        return False


def deprecated(reason, **kw):
    """PEP 702: mark a function or class as deprecated.

    The decorator RETURNS A WRAPPER that warns and then calls through, and it
    records the reason on `__deprecated__` -- which a type checker reads
    statically and a program can read back.
    """
    def deco(target):
        def wrapper(*args, **more):
            warn(reason, kw.get("category", DeprecationWarning))
            return target(*args, **more)
        wrapper.__deprecated__ = reason
        wrapper.__name__ = target.__name__
        return wrapper
    return deco
