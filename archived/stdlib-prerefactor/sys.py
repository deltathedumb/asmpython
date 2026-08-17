"""The parts of `sys` that are ORDINARY PYTHON.

`maxsize`, `platform` and `implementation` are compiler constants and stay in
the builtin module table; this file is the half that is a list, a counter or a
callback -- state a program creates and reads back, which is Python and not a
property of the build. The two coexist: a name this file does not define keeps
its import and reaches the table as before.
"""

#: The audit hooks, in the order they were added. A list rather than a set:
#: PEP 578 runs them in registration order and a program can tell.
_audit_hooks = []


def addaudithook(hook):
    """Watch every `sys.audit` event from here on.

    NEVER REMOVED, which is PEP 578's rule and not an omission: a hook that
    could be taken off again would let untrusted code silence the auditing it
    was installed to do.
    """
    _audit_hooks.append(hook)


def audit(event, *args):
    """Announce an event to every hook."""
    for hook in _audit_hooks:
        hook(event, args)


class _Events:
    """The event mask constants. Powers of two, so a tool can ask for several
    with one number, and `NO_EVENTS` is the empty set of them."""

    NO_EVENTS = 0
    PY_START = 1
    PY_RESUME = 2
    PY_RETURN = 4
    PY_YIELD = 8
    CALL = 16
    LINE = 32
    INSTRUCTION = 64
    JUMP = 128
    BRANCH = 256
    RAISE = 512
    EXCEPTION_HANDLED = 1024
    PY_UNWIND = 2048
    PY_THROW = 4096
    STOP_ITERATION = 8192


class _Monitoring:
    """PEP 669's tool registry.

    THE REGISTRY IS ALL THAT IS HERE. Registering a callback records it and
    nothing ever calls it -- the compiled program has no instrumentation
    points to fire from -- so a program that installs a tracer sees its
    registration succeed and no events arrive. That is a real gap and is
    stated rather than hidden: the alternative is refusing the import, which
    stops a program that only asks whether the API exists.
    """

    DEBUGGER_ID = 0
    COVERAGE_ID = 1
    PROFILER_ID = 2
    OPTIMIZER_ID = 5
    MISSING = object()

    def __init__(self):
        self.events = _Events()
        self._tools = {}
        self._callbacks = {}

    def use_tool_id(self, tool_id, name):
        if tool_id in self._tools:
            raise ValueError("tool " + str(tool_id) + " is already in use")
        self._tools[tool_id] = name

    def free_tool_id(self, tool_id):
        if tool_id in self._tools:
            del self._tools[tool_id]

    def get_tool(self, tool_id):
        return self._tools[tool_id] if tool_id in self._tools else None

    def register_callback(self, tool_id, event, func):
        was = self._callbacks.get((tool_id, event))
        self._callbacks[(tool_id, event)] = func
        return was

    def set_events(self, tool_id, event_set):
        return None

    def get_events(self, tool_id):
        return 0

    def restart_events(self):
        return None


monitoring = _Monitoring()


def breakpointhook(*args, **kwargs):
    """What `breakpoint()` calls. The default does nothing here: there is no
    debugger to enter, and a program that replaces it is the case that
    matters."""
    return None


class _Console:
    """The real standard stream, as an object a program can read attributes
    off and replace. Its `write` is the runtime's own printing, so the default
    path costs one extra call and nothing else."""

    def __init__(self, name):
        self.name = name
        self.encoding = "utf-8"
        self.errors = "strict"

    def write(self, text):
        print(text, end="")
        return len(text)

    def writelines(self, lines):
        for one in lines:
            self.write(one)

    def flush(self):
        return None

    def isatty(self):
        return False

    def readable(self):
        return False

    def writable(self):
        return True

    def __repr__(self):
        return "<_io.TextIOWrapper name='<" + self.name + ">'>"


stdout = _Console("stdout")
stderr = _Console("stderr")


def _print(*args, **kwargs):
    """`print` ROUTED THROUGH `sys.stdout`, for a program that replaces it.

    Only programs that ASSIGN to `sys.stdout` are rewritten to come here --
    see `bundled.py`. Everything else keeps the direct call, so redirection
    costs the programs that use it and nothing else.
    """
    sep = kwargs["sep"] if "sep" in kwargs else " "
    end = kwargs["end"] if "end" in kwargs else "\n"
    where = kwargs["file"] if "file" in kwargs and kwargs["file"] is not None \
        else stdout
    parts = []
    for one in args:
        parts.append(str(one))
    where.write(sep.join(parts) + end)
    if "flush" in kwargs and kwargs["flush"]:
        where.flush()
