"""The target registry.

Targets are not compiler internals. The compiler asks this registry for a
target by name; adding a platform is a registration, not an edit to the
driver. Built-ins register through exactly the same call a third party makes,
so the extension path is the one that is exercised on every build rather than
the one nobody runs.
"""
from __future__ import annotations

from collections.abc import Iterable

from .base import Target

_REGISTRY: dict[str, Target] = {}
_ALIASES: dict[str, str] = {}


def register(target: Target, *, aliases: Iterable[str] = ()) -> Target:
    """Register `target` under its name, plus any aliases.

    Re-registering a name REPLACES it, so an extension can override a built-in
    platform without registry surgery. That is deliberate and different from
    the backend registry, which refuses duplicates: two backends with one name
    is always a mistake, whereas "the same platform, configured differently"
    is a thing people legitimately want.
    """
    if not target.name:
        raise ValueError("a target needs a name")
    _REGISTRY[target.name] = target
    for alias in aliases:
        _ALIASES[alias] = target.name
    return target


def resolve(name: str) -> str:
    """Canonical name for `name`, following one level of alias."""
    return _ALIASES.get(name, name)


def get(name: str) -> Target:
    load_builtin()
    canonical = resolve(name)
    try:
        return _REGISTRY[canonical]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise LookupError(
            f"unknown target {name!r}\navailable: {known}") from None


def available() -> dict[str, Target]:
    load_builtin()
    return dict(_REGISTRY)


def aliases() -> dict[str, str]:
    load_builtin()
    return dict(_ALIASES)


def host() -> Target:
    """The target matching the machine this is running on.

    Guessing wrong here produces artifacts that assemble and then fail to
    link, so it is derived from the interpreter's own platform rather than
    defaulted to whatever was convenient.
    """
    import sys
    load_builtin()
    if sys.platform == "win32":
        return _REGISTRY["x86_64-windows"]
    if sys.platform == "darwin":
        return _REGISTRY["x86_64-macos"]
    return _REGISTRY["x86_64-linux"]


_loaded = False


def load_builtin() -> None:
    """Register the targets that ship with apc. Idempotent."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    from .. import targets  # noqa: F401  (registers on import)
