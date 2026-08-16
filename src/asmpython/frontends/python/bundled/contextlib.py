"""`contextlib`, as ordinary Python this compiler compiles.

`contextmanager` is a generator wearing the `with` protocol: everything before
the `yield` is `__enter__`, everything after is `__exit__`, and a `try` around
the `yield` is how the block's exception reaches it. Written here, that
sentence IS the implementation.
"""


class _GeneratorContext:
    """One `with` over one generator.

    A FRESH ONE PER CALL, which is why `contextmanager` returns a function
    that builds this rather than the object itself: a context manager entered
    twice needs two generators, and reusing one would resume a body that had
    already finished.
    """

    def __init__(self, gen):
        self.gen = gen

    def __enter__(self):
        return next(self.gen)

    def __exit__(self, kind, value, traceback):
        if value is None:
            # Run the rest of the body. A generator that yields again is
            # trying to be two context managers, which CPython refuses too.
            try:
                next(self.gen)
            except StopIteration:
                return False
            return False
        # THE BLOCK RAISED. Throwing into the generator resumes it AT the
        # yield, so a `try` around it catches the exception exactly as the
        # sentence above promises. Swallowing it there suppresses it, which is
        # what `except ValueError: pass` in the body means.
        try:
            self.gen.throw(value)
        except StopIteration:
            return True
        except BaseException as again:
            if again is value:
                return False
            raise
        return True


def contextmanager(fn):
    """Turn a generator function into a `with`-able one."""
    def helper(*args, **kw):
        return _GeneratorContext(fn(*args, **kw))
    return helper


class suppress:
    """Swallow the named exceptions and let the `with` block end quietly.

    A context manager whose whole content is `__exit__`: returning True is how
    a manager says the exception is handled, and that is the entire mechanism.
    """

    def __init__(self, *exceptions):
        self._exceptions = exceptions

    def __enter__(self):
        return None

    def __exit__(self, kind, value, traceback):
        if kind is None:
            return False
        for want in self._exceptions:
            if issubclass(kind, want):
                return True
        return False


class ExitStack:
    """Cleanups registered as the block runs, undone in REVERSE on the way out.

    Reverse because a later cleanup may depend on what an earlier one set up:
    unwinding in registration order would tear the ground out from under the
    thing standing on it.
    """

    def __init__(self):
        self._callbacks = []

    def callback(self, fn, *args, **kwargs):
        self._callbacks.append((fn, args, kwargs))
        return fn

    def push(self, manager):
        """Take over a manager's `__exit__` WITHOUT entering it."""
        self._callbacks.append((manager.__exit__, (None, None, None), {}))
        return manager

    def enter_context(self, manager):
        made = manager.__enter__()
        self._callbacks.append((manager.__exit__, (None, None, None), {}))
        return made

    def pop_all(self):
        """Move the callbacks to a NEW stack, leaving this one empty.

        How a block hands its cleanups on rather than running them: the old
        stack unwinds nothing because it now holds nothing.
        """
        moved = ExitStack()
        moved._callbacks = self._callbacks
        self._callbacks = []
        return moved

    def close(self):
        self.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        pending = self._callbacks
        self._callbacks = []
        i = len(pending) - 1
        while i >= 0:
            entry = pending[i]
            entry[0](*entry[1], **entry[2])
            i = i - 1
        return False


class nullcontext:
    """A manager that does nothing, so a conditional `with` needs no branch."""

    def __init__(self, enter_result=None):
        self.enter_result = enter_result

    def __enter__(self):
        return self.enter_result

    def __exit__(self, kind, value, traceback):
        return False
