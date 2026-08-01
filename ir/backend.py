"""The backend interface.

A backend turns a verified `Module` into artifacts. The interface is four
things, and three of them are one line each:

    class MyBackend(Backend):
        name = "my-machine"
        def emit(self, module) -> dict[str, bytes]: ...

That is the whole contract. `emit` receives a module that has already passed
`verify()`, so every invariant in `verify.py` holds and a backend needs no
defensive checks -- no "what if the block has no terminator", no "what if this
register was never defined".

WHAT A BACKEND IS ALLOWED TO IGNORE
-----------------------------------
Registers, liveness, and allocation are OPTIONAL. The simplest correct backend
gives every virtual register its own stack slot and never allocates a machine
register at all; `backends/naive.py` does exactly that and is the reference for
what "minimum viable backend" means. It is slow and it is correct, and correct
first is the right order.

When you want speed, `ir.regalloc` is a library you may call. It is not part of
the interface and nothing breaks if you never touch it.

WHY NOT A VISITOR OR A TABLE OF HANDLERS
----------------------------------------
Both were considered. A dispatch table (`{Op.ADD: self.emit_add, ...}`) looks
tidier and is worse to learn from: it forces a beginner to discover the whole
opcode set before writing a line, and a missing entry fails at run time in a
program that seemed to work. A plain `for ins in block.instrs` with a `match`
puts the entire backend on one screen, and an unhandled opcode is a visible
`case _:` rather than an absent dictionary key.
"""
from __future__ import annotations

import abc

from .core import Module


class Backend(abc.ABC):
    """Turn a verified module into named artifacts."""

    #: Selector used by `irc build --backend NAME`. Required.
    name: str = ""
    #: One line, shown by `irc backends`.
    description: str = ""
    #: False for a backend that is a work in progress; `irc` will warn.
    ready: bool = True

    @abc.abstractmethod
    def emit(self, module: Module) -> dict[str, bytes]:
        """Compile `module`. Returns {filename: contents}.

        `module` has passed `verify()`. Every invariant listed at the top of
        verify.py holds; do not re-check them.

        Return one entry for a single artifact (`{"out.s": b"..."}`) or several
        when a target genuinely produces several. The CLI writes them next to
        the requested output path.
        """

    def __repr__(self) -> str:
        return f"<backend {self.name}>"


_REGISTRY: dict[str, Backend] = {}


def register(backend: Backend) -> Backend:
    """Make a backend selectable by name. Returns it, so it can decorate."""
    if not backend.name:
        raise ValueError(f"{type(backend).__name__} has no name")
    if backend.name in _REGISTRY:
        raise ValueError(f"backend {backend.name!r} is already registered")
    _REGISTRY[backend.name] = backend
    return backend


def get(name: str) -> Backend:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise SystemExit(
            f"unknown backend {name!r}\navailable: {known}"
        ) from None


def available() -> dict[str, Backend]:
    return dict(_REGISTRY)


def load_builtin() -> None:
    """Import the backends that ship with the tool, registering them."""
    from .backends import naive  # noqa: F401
