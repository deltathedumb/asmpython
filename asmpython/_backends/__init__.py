"""Registry for built-in scaffolds and third-party compilation backends.

The production ``x86-64`` backend and experimental ``ternary`` backend remain
special-cased by ``driver.py`` because they need bespoke runtime and linker
wiring. ``legacy`` is the older NASM-text pipeline and is not an ``IRBackend``.

Every other planned built-in backend is registered here as an explicit
``ScaffoldBackend``. Scaffolds are discoverable and importable, but every
code-generation operation raises ``NotImplementedError``. Registering a real
backend under the same canonical name deliberately replaces its scaffold.

Third-party implementations can still register through
``asmpython.backend.Backend(...)`` and are routed through the ordinary
compile-then-link ``IRBackend`` path.
"""

from __future__ import annotations

from collections.abc import Iterable


_REGISTRY: dict[str, object] = {}
_ALIASES: dict[str, str] = {}


def register_backend(
    name: str,
    backend: object,
    *,
    aliases: Iterable[str] = (),
) -> None:
    """Register ``backend`` under a canonical name and optional aliases.

    Re-registering a canonical name replaces the previous object. This is
    intentional: a real implementation can replace a built-in scaffold without
    requiring registry surgery or changing the public backend name.
    """

    if not name:
        raise ValueError("backend name must not be empty")
    _REGISTRY[name] = backend
    for alias in aliases:
        if alias and alias != name:
            _ALIASES[alias] = name


def get_backend(name: str) -> object | None:
    """Look up a registered backend by canonical name or alias."""

    backend = _REGISTRY.get(name)
    if backend is not None:
        return backend
    canonical = _ALIASES.get(name)
    if canonical is None:
        return None
    return _REGISTRY.get(canonical)


def registered_names() -> list[str]:
    """Return canonical registered backend names in registration order."""

    return list(_REGISTRY.keys())


def registered_aliases() -> dict[str, str]:
    """Return a copy of alias-to-canonical backend mappings."""

    return dict(_ALIASES)


# Register all planned built-in placeholders only after the registry functions
# exist, avoiding a circular import while keeping registration automatic.
from .scaffolds import register_scaffold_backends as _register_scaffold_backends

_register_scaffold_backends(register_backend)
del _register_scaffold_backends


__all__ = [
    "get_backend",
    "register_backend",
    "registered_aliases",
    "registered_names",
]
