"""Public API for registering third-party target platforms.

A *target* is the platform a program is emitted for -- object format, entry
point, syscall convention, runtime startup. A *backend* is the code generator
that produces it. They are separate registries because they vary
independently: one code generator serves several platforms, and one platform
can be reached by several code generators.

    import asmpython

    asmpython.target.Target(
        name="my_os",
        codegen=MyOSCodegen,
        aliases=("myos",),
    )

Then: `asmpython build myfile.py --target my_os`.

`codegen` is a class constructed as ``codegen(module, use_runtime_lib=...)``
and asked for ``.generate()``, which returns assembly text. Subclassing one of
the shipped targets is the usual way to get one -- a new platform is rarely
more than the syscall numbers and the entry sequence.

Registering a name that already exists replaces it, so a plugin may override a
built-in platform without registry surgery.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def _registry():
    from . import _targets
    return _targets


class Target:
    """Registers a target platform selectable via ``--target name``."""

    def __init__(self, name: str, codegen: Any, *,
                 aliases: Iterable[str] = ()) -> None:
        self.name = name
        self.codegen = codegen
        self.aliases = tuple(aliases)
        _registry().register_target(name, codegen, aliases=self.aliases)

    def __repr__(self) -> str:
        return f"Target(name={self.name!r}, codegen={self.codegen!r})"


def get(name: str) -> Any:
    """The codegen class registered for `name`, following aliases.

    Raises `LookupError` naming what is available, rather than `KeyError`:
    the usual cause is a typo or a plugin that never registered.
    """
    return _registry().get_target(name)


def available() -> list[str]:
    """Every registered target name, built-in and third-party."""
    return _registry().available()


def aliases() -> dict[str, str]:
    """Alias -> canonical name, for everything registered."""
    return _registry().aliases()


__all__ = ["Target", "get", "available", "aliases"]
