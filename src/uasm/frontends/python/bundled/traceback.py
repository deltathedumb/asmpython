"""Formatting an exception -- type, message, chain and the ONE frame this
runtime actually keeps.

COVERAGE: `format_exception_only`, `format_exception`, `print_exception`,
`format_tb`, `print_tb`, `extract_tb`, `StackSummary`, `FrameSummary`,
`TracebackException` and `TracebackException.from_exception` -- exception
chaining (`raise X from Y`, implicit `__context__`, `from None` suppression),
`__notes__`, and the one real frame `e.__traceback__` carries.

NOT COVERED, each refused BY NAME rather than approximated:

`format_exc` and `print_exc` -- the zero-argument forms that read
`sys.exc_info()` in CPython. Neither `sys.exc_info` nor any other way for a
CALLED function to see "the exception my caller is handling" exists here: a
bare `raise` is resolved at COMPILE TIME against a lexical stack of enclosing
`except` blocks in the SAME function (`_dyn_raise`/`self._handling` in
`frontends/python/dynamic.py`), not against runtime thread state. Measured
directly: a helper function containing nothing but `raise`, called from
inside a live `except` block, raises nothing and returns normally, where
CPython re-raises the exception being handled. A module that defined these
two names anyway would look identical to the real ones and quietly do
nothing on the one call shape they exist for.

`walk_tb`, `walk_stack`, `extract_stack`, `format_stack`, `print_stack`,
`clear_frames` -- everything that walks a live CALL STACK rather than an
exception's own traceback link. There is no call stack to walk: a frame is
built once, at the moment an exception is raised, from the position table --
see below.

The quoted SOURCE LINE under each frame, and the PEP 657 caret line under
that. CPython reads it from `linecache`, keyed by `co_filename` -- which
this runtime always answers `<compiled>` (`objects_host._apy_code_of` says
why: the path is a compile-time fact the running program does not carry).
`<compiled>` is never a real file, so a source line can never be found
honestly; this module never tries, rather than printing one CPython would
not.

`SyntaxError`'s own multi-line format (the `^^^^` under the offending token,
computed from `text`/`offset`/`end_offset`) and `BaseExceptionGroup`'s
tree format (`  + Exception Group Traceback ...`, one sub-block per nested
exception) -- both raise `NotImplementedError` naming the exception rather
than falling through to the ordinary one-line format and calling that a
match. `except*` and `ExceptionGroup` are themselves unsettled in this
compiler as of this module (`repr` and matching already disagree with
CPython), so a plausible-looking group traceback here would be built on
ground this module cannot vouch for.

WHAT ONE FRAME MEANS. `e.__traceback__` names exactly the frame the raise
or failing operation ran in -- never its caller. `tb_next` is always None.
For a `raise` in the same frame as the `try` that catches it -- module level,
or one function, no call in between -- that is the whole traceback CPython
would show too, and `extract_tb`/`format_exception` agree with it exactly
(once the file path is set aside, since `<compiled>` never matches a real
one). For a `raise` reached through a call, CPython would show every frame
from the `try` inward; this module shows only the innermost one, which is
real and not fabricated, but is not the whole story CPython tells.

RECORDING A POSITION AT ALL IS OPT-IN, per the frontend
(`dynamic._wants_positions`): a program's own source has to mention
`__traceback__`, `tb_frame`, `tb_lineno`, `tb_next`, `tb_lasti`,
`co_positions` or `f_lineno` somewhere in its text before the compiler
emits the calls that fill in a raised exception's position. This module's
own source does not count -- the splicer inserts a tree, not text -- so a
program that imports `traceback` and never itself names one of those
attributes gets `e.__traceback__ is None` for an exception that WAS raised,
same as one that never was. Every function below still answers something
useful in that case (an empty `StackSummary`, a traceback-free
`format_exception`), because that is what CPython answers for an exception
with no traceback too.

Neither `__module__` exists on an exception TYPE here (a compiler gap, not a
decision -- `hasattr(SomeError, "__module__")` is False for a builtin and a
user class alike), so a formatted exception name is never module-qualified.
That happens to match CPython exactly for every class this rebuild's own
test programs can define, which all live in `__main__` -- CPython does not
qualify those either.
"""


_SENTINEL = object()

_CAUSE_MESSAGE = (
    "\nThe above exception was the direct cause of the "
    "following exception:\n\n")

_CONTEXT_MESSAGE = (
    "\nDuring handling of the above exception, another "
    "exception occurred:\n\n")


def _type_name(exc_type):
    # `__qualname__` is real here; `__module__` is not -- see the module
    # docstring. Falling back to `__name__` covers a type this frontend
    # cannot give a `__qualname__` at all.
    name = getattr(exc_type, "__qualname__", None)
    return name if name is not None else exc_type.__name__


def _guard_unsupported(value):
    """Raise, naming the exception, for the two shapes this module refuses
    rather than approximates. Called wherever a real exception VALUE (not
    just a type) is in hand."""
    if isinstance(value, SyntaxError):
        raise NotImplementedError(
            "traceback: SyntaxError's own multi-line format (the source "
            "line and the '^^^^' under it) is not supported -- refused by "
            "name rather than shown as an ordinary one-line exception")
    if isinstance(value, BaseExceptionGroup):
        raise NotImplementedError(
            "traceback: BaseExceptionGroup/ExceptionGroup's tree format is "
            "not supported -- refused by name rather than shown as an "
            "ordinary one-line exception")


def format_exception_only(exc, value=_SENTINEL):
    """CPython's two calling conventions: `format_exception_only(exc)` with
    `exc` the INSTANCE, or the legacy `format_exception_only(exc_type,
    value)` with `exc` the CLASS. Distinguished by whether `value` was
    passed at all -- `format_exception_only(SomeError, None)` is legal and
    different from not passing a second argument."""
    if value is _SENTINEL:
        evalue = exc
    else:
        evalue = value
    _guard_unsupported(evalue)
    name = _type_name(type(evalue))
    text = str(evalue)
    line = (name + ": " + text + "\n") if text else (name + "\n")
    out = [line]
    notes = getattr(evalue, "__notes__", None)
    if notes:
        for note in notes:
            text = str(note)
            for part in text.split("\n"):
                out.append(part + "\n")
    return out


class FrameSummary:
    """One entry of a `StackSummary`. `line` is always None -- see the
    module docstring on why a source line can never be read here."""

    def __init__(self, filename, lineno, name, end_lineno=None,
                 colno=None, end_colno=None):
        self.filename = filename
        self.lineno = lineno
        self.end_lineno = end_lineno if end_lineno is not None else lineno
        self.colno = colno
        self.end_colno = end_colno if end_colno is not None else colno
        self.name = name
        self.line = None

    def __repr__(self):
        return "<FrameSummary file %s, line %d in %s>" % (
            self.filename, self.lineno, self.name)


class StackSummary(list):
    """A list of `FrameSummary`, oldest call first -- matching CPython's
    order even though this runtime's chain is never longer than one."""

    @classmethod
    def _extract(cls, tb, limit=None):
        frames = cls()
        cur = tb
        while cur is not None and hasattr(cur, "tb_frame"):
            frame = cur.tb_frame
            code = frame.f_code
            positions = None
            rows = getattr(code, "_positions", None)
            if rows:
                for row in rows:
                    if row[0] == cur.tb_lineno:
                        positions = row
                        break
            frames.append(FrameSummary(
                code.co_filename, cur.tb_lineno, code.co_name,
                end_lineno=positions[1] if positions else None,
                colno=positions[2] if positions else None,
                end_colno=positions[3] if positions else None))
            cur = cur.tb_next
        if limit is not None:
            if limit >= 0:
                del frames[limit:]
            else:
                del frames[:len(frames) + limit]
        return frames

    def format(self):
        out = []
        for f in self:
            out.append('  File "%s", line %s, in %s\n' % (
                f.filename, f.lineno, f.name))
        return out


def extract_tb(tb, limit=None):
    return StackSummary._extract(tb, limit=limit)


def format_tb(tb, limit=None):
    return extract_tb(tb, limit=limit).format()


def print_tb(tb, limit=None, file=None):
    text = "".join(format_tb(tb, limit=limit))
    if file is not None:
        file.write(text)
    else:
        print(text, end="")


class TracebackException:
    """What CPython's real class is: an exception's type, message, chain
    and traceback, held apart from the exception object itself so
    formatting one does not need it to still be alive. Built from
    `e.__traceback__`, `e.__cause__`, `e.__context__` and
    `e.__suppress_context__`, all of which are real on this runtime -- see
    the module docstring for what the traceback HALF is missing."""

    def __init__(self, exc_type, exc_value, exc_traceback, *, limit=None,
                 chain=True, _seen=None):
        _guard_unsupported(exc_value)
        self.exc_type = exc_type
        self._str = str(exc_value)
        self.__notes__ = getattr(exc_value, "__notes__", None)
        self.stack = extract_tb(exc_traceback, limit=limit)
        self.__cause__ = None
        self.__context__ = None
        self.__suppress_context__ = getattr(
            exc_value, "__suppress_context__", False)
        if _seen is None:
            _seen = set()
        _seen.add(id(exc_value))
        if chain:
            cause = getattr(exc_value, "__cause__", None)
            context = getattr(exc_value, "__context__", None)
            if cause is not None and id(cause) not in _seen:
                self.__cause__ = TracebackException.from_exception(
                    cause, limit=limit, chain=chain, _seen=_seen)
            elif (context is not None and not self.__suppress_context__
                  and id(context) not in _seen):
                self.__context__ = TracebackException.from_exception(
                    context, limit=limit, chain=chain, _seen=_seen)

    @classmethod
    def from_exception(cls, exc, *, limit=None, chain=True, _seen=None):
        return cls(type(exc), exc, getattr(exc, "__traceback__", None),
                    limit=limit, chain=chain, _seen=_seen)

    def format_exception_only(self):
        name = _type_name(self.exc_type)
        text = self._str
        line = (name + ": " + text + "\n") if text else (name + "\n")
        out = [line]
        if self.__notes__:
            for note in self.__notes__:
                for part in str(note).split("\n"):
                    out.append(part + "\n")
        return out

    def format(self, *, chain=True):
        out = []
        if chain:
            if self.__cause__ is not None:
                out.extend(self.__cause__.format(chain=chain))
                out.append(_CAUSE_MESSAGE)
            elif self.__context__ is not None:
                out.extend(self.__context__.format(chain=chain))
                out.append(_CONTEXT_MESSAGE)
        if self.stack:
            out.append("Traceback (most recent call last):\n")
            out.extend(self.stack.format())
        out.extend(self.format_exception_only())
        return out


def format_exception(exc, value=_SENTINEL, tb=_SENTINEL, limit=None,
                      chain=True):
    """The modern one-argument call (`exc` an INSTANCE) and the legacy
    three-argument one (`exc` a CLASS, `value` the instance, `tb` its
    traceback) -- distinguished the same way `format_exception_only` is."""
    if value is _SENTINEL:
        exc_type = type(exc)
        exc_value = exc
        exc_traceback = getattr(exc, "__traceback__", None)
    else:
        exc_type = exc
        exc_value = value
        exc_traceback = None if tb is _SENTINEL else tb
    te = TracebackException(exc_type, exc_value, exc_traceback,
                            limit=limit, chain=chain)
    return te.format(chain=chain)


def print_exception(exc, value=_SENTINEL, tb=_SENTINEL, limit=None,
                     file=None, chain=True):
    """Writes to `file` if given. CPython defaults to `sys.stderr`; that
    object does not exist on this runtime (`sys.stdout`/`sys.stderr` are
    both unavailable -- a compiler gap, not this module's), so with no
    `file` this prints to stdout instead of silently doing nothing."""
    text = "".join(format_exception(exc, value, tb, limit=limit,
                                     chain=chain))
    if file is not None:
        file.write(text)
    else:
        print(text, end="")
