"""Issue a warning without stopping the program.

COVERAGE: `warn`, `warn_explicit`, `showwarning`, `formatwarning`,
`filterwarnings`, `simplefilter`, `resetwarnings`, `catch_warnings`,
`filters`, `WarningMessage`, `deprecated`.

NOT COVERED: `onceregistry` as a public name, `skip_file_prefixes`, and the
`__warningregistry__` a module gets for "once"/"default" bookkeeping -- so
`once` and `default` behave as `always` here. Every one of those needs frame
inspection this frontend does not have, and a filter that silently keeps a
warning is better than one that silently drops it.

`deprecated` covers the two cases programs use -- a deprecated function and a
deprecated class -- and NOT the warning CPython issues when something
SUBCLASSES a deprecated class. See its own docstring for why.

WHY THIS IS THE FIRST MODULE OF THE REBUILD. It was not chosen: the embedded
compiler needs it. `_pycompile.py` calls `warnings.warn(msg, SyntaxWarning,
stacklevel=2)`, so archiving the old standard library broke the one part of
`bundled/` that was deliberately kept -- which is what measuring the archive
was for. See `docs/STDLIB.md`.

THE FORMAT IS OBSERVABLE and is CPython's exactly:

    f.py:12: UserWarning: m

with the source line, indented two spaces, on a second line when there is one.
Programs parse this, and a message that differs by a colon is a message that
does not match.
"""
import sys


class WarningMessage:
    """One warning, as `catch_warnings(record=True)` hands it over."""

    _WARNING_DETAILS = ("message", "category", "filename", "lineno", "file",
                        "line", "source")

    def __init__(self, message, category, filename, lineno, file=None,
                 line=None, source=None):
        self.message = message
        self.category = category
        self.filename = filename
        self.lineno = lineno
        self.file = file
        self.line = line
        self.source = source
        self._category_name = category.__name__ if category else None

    def __str__(self):
        return ("{message : %r, category : %r, filename : %r, lineno : %s, "
                "line : %r}" % (self.message, self._category_name,
                                self.filename, self.lineno, self.line))


#: The filter list, newest first -- `filterwarnings` INSERTS at the front, so
#: a rule added later wins over one added earlier. Each entry is
#: (action, message, category, module, lineno), and `None` for message or
#: module means "matches anything", which is what lets the common calls stay
#: short.
filters = []

defaultaction = "default"
onceregistry = {}

_ACTIONS = ("default", "error", "ignore", "always", "module", "once")


def _actions_ok(action):
    if action not in _ACTIONS:
        raise ValueError("invalid action: %r" % (action,))


def filterwarnings(action, message="", category=Warning, module="",
                   lineno=0, append=False):
    """Insert a rule. Front by default, so the newest rule wins.

    `message` and `module` are REGULAR EXPRESSIONS in CPython and are matched
    as plain prefixes here, because `re` does not exist yet -- the one place
    this module knowingly differs, and it differs by accepting more rather
    than fewer warnings through.
    """
    _actions_ok(action)
    item = (action, message or None, category, module or None, lineno)
    if append:
        if item not in filters:
            filters.append(item)
    else:
        while item in filters:
            filters.remove(item)
        filters.insert(0, item)


def simplefilter(action, category=Warning, lineno=0, append=False):
    """A rule matching every message and every module."""
    _actions_ok(action)
    item = (action, None, category, None, lineno)
    if append:
        if item not in filters:
            filters.append(item)
    else:
        while item in filters:
            filters.remove(item)
        filters.insert(0, item)


def resetwarnings():
    """Drop every filter, including the ones installed at startup."""
    del filters[:]


def _match(pattern, text):
    """CPython matches these as regular expressions, anchored at the start.

    A prefix test is the honest approximation until `re` exists: every
    pattern without metacharacters means the same thing, and one with them
    matches less than it should -- which lets a warning through rather than
    suppressing one the program asked to see.
    """
    return pattern is None or text.startswith(pattern)


def _filter_for(message, category, module, lineno):
    for action, pattern, cat, mod, ln in filters:
        if (_match(pattern, str(message)) and issubclass(category, cat)
                and _match(mod, module) and (ln == 0 or ln == lineno)):
            return action
    return defaultaction


def formatwarning(message, category, filename, lineno, line=None):
    """The exact text CPython writes. See the module docstring."""
    out = "%s:%s: %s: %s\n" % (filename, lineno, category.__name__, message)
    if line:
        # A WHITESPACE-ONLY SOURCE LINE STILL PRINTS, as two spaces and a
        # newline. CPython tests the line itself and not its stripped form,
        # so `line="   "` yields `m\n  \n`. Skipping it when nothing survives
        # the strip looks tidier and is a DIFFERENT STRING -- which is what a
        # program parsing this output notices and a golden file would have
        # enshrined.
        out = out + "  " + line.strip() + "\n"
    return out


def showwarning(message, category, filename, lineno, file=None, line=None):
    """Write it. Replaceable: a program may assign its own and be obeyed."""
    if file is None:
        file = sys.stderr
        if file is None:
            return
    text = formatwarning(message, category, filename, lineno, line)
    try:
        file.write(text)
    except OSError:
        # A CLOSED STDERR IS NOT AN ERROR HERE. Warning about a warning is a
        # loop, and losing the message is what CPython does too.
        pass


def warn_explicit(message, category, filename, lineno, module=None,
                  registry=None, module_globals=None, source=None):
    """Issue a warning at a stated position, with no frame inspection."""
    if module is None:
        module = filename or "<unknown>"
        if module.endswith(".py"):
            module = module[:-3]
    if isinstance(message, Warning):
        category = message.__class__
    action = _filter_for(message, category, module, lineno)
    if action == "ignore":
        return
    if action == "error":
        if isinstance(message, Warning):
            raise message
        raise category(message)
    # `once`, `default` and `module` all need a per-module registry keyed on
    # the message, which needs the caller's globals. See the coverage note.
    if not isinstance(message, Warning):
        message = category(message)
    _record_or_show(message, category, filename, lineno, source)


def warn(message, category=UserWarning, stacklevel=1, source=None):
    """Issue a warning.

    `stacklevel` IS ACCEPTED AND NOT USED. CPython walks that many frames up
    to blame the caller rather than the library, which needs frame
    inspection; here every warning is attributed to the program. Accepting it
    silently is the right trade: refusing would break every correct call, and
    the argument only ever changes which filename is printed.
    """
    if isinstance(message, Warning):
        category = message.__class__
    elif category is None:
        category = UserWarning
    warn_explicit(message, category, "<program>", 0, None, None, None, source)


_recording = []


def _record_or_show(message, category, filename, lineno, source):
    if _recording:
        _recording[-1].append(WarningMessage(message, category, filename,
                                             lineno, None, None, source))
        return
    showwarning(message, category, filename, lineno, None, None)


class catch_warnings:
    """Save and restore the filter state, optionally recording warnings.

    `record=True` collects them into the list this yields instead of writing
    them, which is how a test asserts a warning was issued without the text
    reaching a terminal.
    """

    def __init__(self, record=False, module=None, action=None,
                 category=Warning, lineno=0, append=False):
        self._record = record
        self._entered = False
        self._action = action
        self._category = category
        self._lineno = lineno
        self._append = append

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Cannot enter %r twice" % self)
        self._entered = True
        self._saved = filters[:]
        self._saved_show = showwarning
        if self._action is not None:
            simplefilter(self._action, self._category, self._lineno,
                         self._append)
        if self._record:
            collected = []
            _recording.append(collected)
            return collected
        return None

    def __exit__(self, *exc):
        if not self._entered:
            raise RuntimeError("Cannot exit %r without entering first" % self)
        del filters[:]
        filters.extend(self._saved)
        if self._record:
            _recording.pop()
        return False


def _wraps(inner, fn):
    """`functools.wraps`, written out here rather than imported.

    NOT DUPLICATION FOR ITS OWN SAKE: `_pycompile` imports this module for one
    `SyntaxWarning`, so anything `warnings` imports is spliced into EVERY
    program that calls `compile`, `eval` or `exec`. Importing `functools` for
    one function put all of it -- `lru_cache`, `singledispatch`, `partial` and
    the rest -- into those programs, for the three lines below.

    WHAT IT DID NOT DO IS SPEED THEM UP, and that was the reason it was tried.
    An `eval` program was measured at 17.4s with the import and 17.2s without:
    the cost of those programs is the embedded compiler itself, roughly 3,300
    lines of `_pylex`, `_pyparse`, `_pyvalidate`, `_pycompile` and `_pyrun`,
    and one more module is lost in it. Recorded because the next person to
    find `eval` slow should start there and not here.

    So this stands on the smaller claim: a module spliced into every such
    program should not carry a dependency it uses three lines of. A program
    that wants the real `functools.wraps` imports it and pays deliberately.
    """
    inner.__name__ = fn.__name__
    inner.__doc__ = fn.__doc__
    inner.__wrapped__ = fn
    return inner


class deprecated:
    """PEP 702. Mark a function or a class deprecated, and say so when used.

    A DECORATOR THAT IS A CLASS, because it is two things at once: the
    `__deprecated__` attribute it leaves behind is what a type checker reads
    without running anything, and the warning is what a program gets when it
    reaches the deprecated code anyway. Neither substitutes for the other.

        @deprecated("use spam() instead")
        def ham(): ...

    `category=None` sets the attribute and issues NOTHING. That is the spelling
    for something deprecated in the documentation but not yet noisy, and it is
    the whole reason the parameter accepts None rather than being a flag.

    ON A CLASS THIS WRAPS `__init__`, and CPython wraps `__new__`. The
    difference is not a shortcut: this object model has no `__new__` to
    replace -- `C.__new__` on a class that does not define one is an
    AttributeError here, where CPython finds `object.__new__` -- so there is
    nothing to intercept. `__init__` runs for exactly the same instantiations,
    and the `type(self) is target` guard is what keeps a SUBCLASS from
    warning, which is the rule CPython states as `if cls is arg`.

    WHAT THAT DOES NOT GET is CPython's second warning, the one issued when a
    class SUBCLASSES a deprecated one. That rides on `__init_subclass__`, which
    this object model does not have either, and there is no honest way to fake
    it: nothing runs at the moment a class statement names its base. So
    `class D(C)` is silent here and warns under CPython, and that is written
    down rather than discovered.
    """

    def __init__(self, message, /, *, category=DeprecationWarning,
                 stacklevel=1):
        if not isinstance(message, str):
            raise TypeError("Expected an object of type str for 'message', "
                            "not %r" % (type(message).__name__,))
        self.message = message
        self.category = category
        self.stacklevel = stacklevel

    def __call__(self, arg, /):
        # READ OFF `self` FIRST, exactly as CPython does, so that the closures
        # below capture the three values and not the decorator object. A
        # wrapper holding `self` keeps the `deprecated` instance alive for as
        # long as the function it decorates, which is forever.
        msg = self.message
        category = self.category
        stacklevel = self.stacklevel
        if category is None:
            arg.__deprecated__ = msg
            return arg
        # A CLASS IS CALLABLE, so this test comes first or every class takes
        # the function path and gets wrapped into something that is no longer
        # a class.
        if isinstance(arg, type):
            inner = getattr(arg, "__init__", None)

            def __init__(self, *args, **kwargs):
                # ONLY FOR THE DEPRECATED CLASS ITSELF. A subclass inherits
                # this `__init__` and is not the thing that was deprecated;
                # CPython spells the same rule `if cls is arg`.
                if type(self) is arg:
                    warn(msg, category, stacklevel + 1)
                if inner is not None:
                    return inner(self, *args, **kwargs)
                return None

            arg.__init__ = __init__
            arg.__deprecated__ = msg
            return arg
        if callable(arg):
            def wrapper(*args, **kwargs):
                warn(msg, category, stacklevel + 1)
                return arg(*args, **kwargs)

            _wraps(wrapper, arg)
            # BOTH OF THEM. The wrapper is what callers see, and `arg` is what
            # `__wrapped__` hands back -- a tool that unwraps to find the real
            # function must not lose the mark by doing so.
            arg.__deprecated__ = msg
            wrapper.__deprecated__ = msg
            return wrapper
        raise TypeError("@deprecated decorator with non-None category must "
                        "be applied to a class or callable, not %r" % (arg,))


#: THE STARTUP FILTERS, which are CPython's own. Without them a
#: DeprecationWarning is visible everywhere rather than only in `__main__`,
#: and a library that deprecates something floods every program that uses it.
simplefilter("default")
filterwarnings("ignore", category=DeprecationWarning)
filterwarnings("default", category=DeprecationWarning, module="__main__")
filterwarnings("ignore", category=PendingDeprecationWarning)
filterwarnings("ignore", category=ImportWarning)
filterwarnings("ignore", category=ResourceWarning)
