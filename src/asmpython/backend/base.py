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


class Backend(abc.ABC):
    """Turn a verified module into named artifacts."""

    name: str = ""
    description: str = ""
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
    from ..backends import c, x86_64  # noqa: F401


class BackendUnsupported(Exception):
    """A backend cannot compile this program for this target.

    Distinct from a crash and from a user error: the program is valid and the
    compiler is working, but this particular code generator does not implement
    the construct yet. The driver turns it into a diagnostic naming the
    backend and the target, so the answer ("use --backend c") is visible
    rather than something to work out from a traceback.
    """
