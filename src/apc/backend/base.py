"""The backend interface and target description.

A backend turns a verified `Module` into artifacts. The interface is one
abstract method, because the module it receives has already passed `verify()`
and every invariant listed there holds -- a backend needs no defensive checks.

    class MyBackend(Backend):
        name = "my-machine"
        def emit(self, module, target) -> dict[str, bytes]: ...

REGISTER ALLOCATION IS OPTIONAL. The simplest correct backend gives every
virtual register its own stack slot and never allocates. `apc.backend.regalloc`
is a library a backend may call; it is not a stage anyone must implement, and
`backends/c` never touches it.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass

from ..ir import Module


@dataclass(frozen=True, slots=True)
class Target:
    """What a backend needs to know about the machine it is emitting for.

    Passed in rather than baked into a backend so that one code generator can
    serve several configurations -- 32- and 64-bit, little- and big-endian --
    without a fork. A backend that genuinely supports only one shape simply
    ignores the fields it does not vary over.
    """

    name: str
    pointer_size: int = 8
    little_endian: bool = True
    #: Stack alignment required at a call boundary, in bytes.
    stack_alignment: int = 16
    #: Object format the linker stage should produce.
    object_format: str = "elf"

    @property
    def pointer_bits(self) -> int:
        return self.pointer_size * 8


HOST_X86_64_LINUX = Target("x86_64-linux", object_format="elf")
HOST_X86_64_WINDOWS = Target("x86_64-windows", object_format="coff")
PORTABLE_C = Target("c", object_format="source")


class Backend(abc.ABC):
    """Turn a verified module into named artifacts."""

    name: str = ""
    description: str = ""
    #: False for a work in progress; the driver warns.
    ready: bool = True
    #: Default target if the user names none.
    default_target: Target = PORTABLE_C

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
