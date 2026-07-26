"""Catalog and registration for planned, intentionally unimplemented frontends.

The default ``python`` frontend is the only real implementation today (see
``python_frontend.py``); it is registered directly by the package, not from
here. Every other planned source language is registered as an explicit
``ScaffoldFrontend`` so ``--frontend <lang>`` is discoverable and fails loudly
rather than silently. Registering a real frontend under the same canonical name
deliberately replaces its scaffold -- exactly as backends do.
"""

from __future__ import annotations

from collections.abc import Callable

from ._scaffold import FrontendScaffoldSpec, ScaffoldFrontend


SCAFFOLD_FRONTEND_SPECS: tuple[FrontendScaffoldSpec, ...] = (
    FrontendScaffoldSpec(
        "lua", "Lua", "scripting", aliases=("Lua", "luau"),
        source_extensions=(".lua", ".luau"),
    ),
    FrontendScaffoldSpec(
        "javascript", "JavaScript", "scripting",
        aliases=("JavaScript", "js", "ecmascript"),
        source_extensions=(".js", ".mjs"),
    ),
    FrontendScaffoldSpec(
        "typescript", "TypeScript", "scripting", aliases=("TypeScript", "ts"),
        source_extensions=(".ts",),
    ),
    FrontendScaffoldSpec(
        "c", "C", "systems", aliases=("C",),
        source_extensions=(".c", ".h"),
    ),
    FrontendScaffoldSpec(
        "go", "Go", "systems", aliases=("Go", "golang"),
        source_extensions=(".go",),
    ),
)

SCAFFOLD_FRONTENDS: dict[str, ScaffoldFrontend] = {
    spec.name: ScaffoldFrontend(spec) for spec in SCAFFOLD_FRONTEND_SPECS
}


def register_scaffold_frontends(register: Callable[..., None]) -> None:
    """Register every canonical scaffold and its convenience aliases."""

    for spec in SCAFFOLD_FRONTEND_SPECS:
        register(spec.name, SCAFFOLD_FRONTENDS[spec.name], aliases=spec.aliases)


__all__ = [
    "SCAFFOLD_FRONTENDS",
    "SCAFFOLD_FRONTEND_SPECS",
    "register_scaffold_frontends",
]
