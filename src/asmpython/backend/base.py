"""The backend interface and target description.

A backend turns a verified `Module` into artifacts. The interface is one
abstract method, because the module it receives has already passed `verify()`
and every invariant listed there holds -- a backend needs no defensive checks.

    class MyBackend(Backend):
        name = "my-machine"
        def emit(self, module, target) -> dict[str, bytes]: ...

REGISTER ALLOCATION IS OPTIONAL. The simplest correct backend gives every
virtual register its own stack slot and never allocates. `asmpython.backend.regalloc`
is a library a backend may call; it is not a stage anyone must implement, and
`backends/c` never touches it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from ..ir import Module
# Re-exported so a backend author needs one import. The TYPE belongs to the
# backend interface -- every `emit` receives one -- but the INSTANCES do not
# live here: they are registered in `asmpython.targets`, so adding a platform never
# means editing the compiler. See docs/TARGETS.md.
from ..target import Target

#: The symbol a backend emits the IR's `main` under.
#:
#: The IR's `main` is not C's `main` -- it returns i64 where C requires int --
#: so a backend that emitted it verbatim would collide with the entry point in
#: whatever runtime gets linked alongside. Named here because the backend
#: writing the symbol and the runtime calling it must agree, and two constants
#: that must agree are one constant.
ENTRY_SYMBOL = "asmpython_main"


@dataclass(frozen=True, slots=True)
class Option:
    """One command-line option a backend takes.

    Declared rather than added to the driver's parser, for the reason every
    other extension point here exists: a backend that ships outside this
    repository gets its flags the same way a built-in does, and `asmpython
    backends` can say what a backend accepts without the driver knowing what
    any of them mean.
    """

    #: As typed, without the dashes: "class-version" is `--class-version`.
    name: str
    help: str
    #: What the value is called in `--help`.
    metavar: str = "VALUE"

    @property
    def flag(self) -> str:
        return "--" + self.name


class OptionError(Exception):
    """A backend option nobody can act on.

    Distinct from a crash: the value is the user's and the message says what
    was wrong with it, so the driver reports it as a diagnostic rather than
    letting a traceback out.
    """


class Backend(abc.ABC):
    """Turn a verified module into named artifacts."""

    name: str = ""
    description: str = ""
    #: Options this backend takes from the command line. The driver turns each
    #: into a flag and hands the values back through `configure`.
    options: tuple[Option, ...] = ()
    #: False for a work in progress; the driver warns.
    ready: bool = True
    #: Name of the target used when the user names none. A NAME, not a
    #: Target: holding an instance here would import the built-in targets
    #: to define the backend interface, putting platforms back inside the
    #: compiler.
    default_target: str = "c"
    #: True if the artifacts already form a complete program -- an entry point
    #: and every host function the frontend calls. The C backend is: it emits
    #: its own `main` wrapper and its own `print_int`. A machine backend is
    #: not, and the link stage supplies the runtime for it. Getting this wrong
    #: produces either a duplicate-symbol error or an undefined one, both at
    #: link time and both clear.
    self_contained: bool = False
    #: `apy_*` THIS BACKEND DEFINES ITSELF, and does not want supplied as IR.
    #:
    #: Part of the object runtime is now written in asmpython's own machine
    #: subset and compiled into every program that needs it
    #: (`link/objects_ir.py`), which is how a backend stops having to define
    #: 229 functions. THIS IS THE OPT-OUT, and it is not an afterthought: the
    #: point of that work is to REMOVE an obligation, not to replace it with a
    #: different one. A backend with its own implementation of a function --
    #: hand-written, faster, or simply older and trusted -- names it here and
    #: keeps it, and the splice supplies only what is left.
    #:
    #: Empty for every built-in backend, which is what makes the shipped
    #: arrangement the ported one. `Options.object_runtime = "c"` is the same
    #: opt-out for a whole BUILD rather than for a backend, and either one
    #: leaves the C runtime exactly as it was before any of it was ported.
    object_runtime: frozenset[str] = frozenset()
    #: HOST SERVICE GROUPS THIS BACKEND PROVIDES -- "file", "net", "time",
    #: "random", "env", "text". See `link/hostsvc.py` for the operations in
    #: each and for why they are optional rather than part of the floor.
    #:
    #: EMPTY IS A COMPLETE BACKEND. That is the whole reason this is declared
    #: rather than assumed: the platform floor is three functions because
    #: making it five was a cost every backend paid forever, and a filesystem
    #: is not something a bare-metal target has. A program that opens a file
    #: is refused HERE, at compile time, naming the operation and the
    #: capability -- not at link time as an undefined symbol, and not at run
    #: time as a wrong answer.
    host_services: frozenset[str] = frozenset()
    #: MODULES THIS BACKEND MAKES IMPORTABLE, as {name: {member: spec}} in the
    #: shape `frontends/python/modules.py` documents.
    #:
    #: A backend targeting a board can offer the board: `import hw` reads a
    #: pin. Which is fine until two backends, or a backend and the standard
    #: set, both want the name `json` -- so the rule is:
    #:
    #:   * `import <backend>.<name>` ALWAYS reaches this backend's module,
    #:     collision or not. The prefixed path is not a fallback, it is the
    #:     real name and it always works.
    #:   * `import <name>` reaches it only if nothing else already has that
    #:     name. A pre-existing module WINS, silently and deliberately: a
    #:     program that said `import json` before a backend grew one of its
    #:     own must keep meaning what it meant.
    #:
    #: So a backend author picks any name they like and the collision resolves
    #: itself, at the cost of the prefix for the loser -- which is exactly
    #: where the ambiguity was.
    modules: dict[str, dict] = {}

    def configure(self, values: dict[str, str], sink) -> "Backend":
        """The backend to emit with, given this run's option values.

        `values` holds only the options this backend declared, keyed by name
        without the dashes. Raise `OptionError` for a value that cannot be
        used; report anything advisory to `sink`.

        RETURN A NEW INSTANCE rather than mutating `self`. The registry holds
        one shared backend object, so a backend that stored its flags on itself
        would leak them into the next compilation in the same process -- which
        is invisible in a command-line run and wrong in every test suite and
        every embedding tool.

        The default ignores both arguments, which is right for a backend with
        no options: the driver rejects a flag no backend declared before it
        ever gets here.
        """
        return self

    def check_host_services(self, module: Module) -> None:
        """Refuse a program that needs a capability this backend has not got.

        HERE RATHER THAN AT LINK TIME, which is the whole point. A frontend
        emits `host_file_open` as an ordinary external call, so without this
        the failure is an undefined symbol naming an object file -- or, for a
        backend that resolves lazily, nothing at all until the program runs.
        Neither says "this target has no filesystem".

        NAMED BY CAPABILITY AND NOT ONLY BY FUNCTION, because the answer is
        never to implement one operation: a target with files has all of them
        or none. Telling an author that `host_file_open` is missing invites
        them to add one function; telling them the `file` group is missing
        says what the work actually is.

        The `core` group is not checked. It is the platform floor, every
        backend owes it, and a backend that has not implemented it fails in
        ways this could only describe worse.
        """
        from ..link import hostsvc

        wanted: dict[str, str] = {}
        for fn in module.functions:
            if not fn.external:
                continue
            group = hostsvc.group_of(fn.name)
            if group is not None and group not in hostsvc.MANDATORY                     and group not in self.host_services:
                wanted.setdefault(group, fn.name)
        if not wanted:
            return
        listed = ", ".join(
            f"{g!r} (for {wanted[g]})" for g in sorted(wanted))
        raise BackendUnsupported(
            f"this program needs host services the {self.name} backend does "
            f"not provide: {listed}. See link/hostsvc.py for what each group "
            f"is, and `Backend.host_services` for how a backend declares one.")

    @abc.abstractmethod
    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        """Compile `module`. Returns {filename: contents}.

        `module` has passed verify(). Do not re-check its invariants.
        """

    def __repr__(self) -> str:
        return f"<backend {self.name}>"


_REGISTRY: dict[str, Backend] = {}


def register(be: Backend) -> Backend:
    if not be.name:
        raise ValueError(f"{type(be).__name__} has no name")
    if be.name in _REGISTRY:
        raise ValueError(f"backend {be.name!r} is already registered")
    _REGISTRY[be.name] = be
    return be


def get(name: str) -> Backend:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise SystemExit(f"unknown backend {name!r}\navailable: {known}") from None


def available() -> dict[str, Backend]:
    return dict(_REGISTRY)


def load_builtin() -> None:
    from ..backends import arm64, c, jvm, x86_64  # noqa: F401


class BackendUnsupported(Exception):
    """A backend cannot compile this program for this target.

    Distinct from a crash and from a user error: the program is valid and the
    compiler is working, but this particular code generator does not implement
    the construct yet. The driver turns it into a diagnostic naming the
    backend and the target, so the answer ("use --backend c") is visible
    rather than something to work out from a traceback.
    """
