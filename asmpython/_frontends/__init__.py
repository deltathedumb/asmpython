"""Registry for the built-in Python frontend, scaffolds, and third-party
source-language frontends.

A frontend is the mirror image of a backend: it turns source text of some
language into the typed module the IR pipeline consumes (see
``asmpython._compiler.ir.IRFrontend``). ``--frontend NAME`` selects one; the
default is ``python``.

The real ``python`` frontend is registered eagerly here. Every other planned
language is an explicit ``ScaffoldFrontend`` -- discoverable and importable, but
every parse raises ``NotImplementedError``. Registering a real frontend under
the same canonical name deliberately replaces its scaffold.

Third-party implementations register through ``asmpython.frontend.Frontend(...)``
and are routed through the ordinary ``IRFrontend.parse`` path.
"""

from __future__ import annotations

from collections.abc import Iterable


_REGISTRY: dict[str, object] = {}
_ALIASES: dict[str, str] = {}


def register_frontend(
    name: str,
    frontend: object,
    *,
    aliases: Iterable[str] = (),
) -> None:
    """Register ``frontend`` under a canonical name and optional aliases.

    Re-registering a canonical name replaces the previous object, so a real
    implementation can replace a built-in scaffold without registry surgery.
    """

    if not name:
        raise ValueError("frontend name must not be empty")
    _REGISTRY[name] = frontend
    for alias in aliases:
        if alias and alias != name:
            _ALIASES[alias] = name


def get_frontend(name: str) -> object | None:
    """Look up a registered frontend by canonical name or alias."""

    frontend = _REGISTRY.get(name)
    if frontend is not None:
        return frontend
    canonical = _ALIASES.get(name)
    if canonical is None:
        return None
    return _REGISTRY.get(canonical)


def registered_names() -> list[str]:
    """Return canonical registered frontend names in registration order."""

    return list(_REGISTRY.keys())


def registered_aliases() -> dict[str, str]:
    """Return a copy of alias-to-canonical frontend mappings."""

    return dict(_ALIASES)


# Register scaffolds first, then let the real python frontend replace its
# (absent) placeholder -- python has no scaffold spec, it is registered outright.
from .scaffolds import register_scaffold_frontends as _register_scaffold_frontends

_register_scaffold_frontends(register_frontend)
del _register_scaffold_frontends

from .python_frontend import PythonFrontend as _PythonFrontend

register_frontend("python", _PythonFrontend(), aliases=("Python", "py"))
del _PythonFrontend


__all__ = [
    "get_frontend",
    "register_frontend",
    "registered_aliases",
    "registered_names",
]
