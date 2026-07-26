"""Hard-fail scaffolding for planned ASMPython source-language frontends.

A scaffold is intentionally discoverable through ``--frontend`` while making no
parsing claim. Every executable operation raises ``NotImplementedError`` until a
real frontend replaces the scaffold. Mirrors ``_backends/_scaffold.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from .._compiler.ir import FrontendContext, IRFrontend


@dataclass(frozen=True)
class FrontendScaffoldSpec:
    """Static identity and planned surface for one future frontend."""

    name: str
    display_name: str
    category: str
    aliases: tuple[str, ...] = ()
    source_extensions: tuple[str, ...] = ()
    notes: str = ""


class ScaffoldFrontend(IRFrontend):
    """An importable frontend placeholder that can never parse a module."""

    is_scaffold = True
    implemented = False
    production_suitable = False

    def __init__(self, spec: FrontendScaffoldSpec) -> None:
        self.spec = spec
        self.name = spec.name
        self.source_extensions = spec.source_extensions

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.spec.aliases

    def unavailable(self, operation: str) -> NoReturn:
        raise NotImplementedError(
            f"ASMPython frontend {self.name!r} ({self.spec.display_name}) is "
            f"scaffolded only; {operation} is not implemented"
        )

    def parse(self, src: str, ctx: FrontendContext) -> object:
        self.unavailable("source parsing")

    def __repr__(self) -> str:
        return f"ScaffoldFrontend(name={self.name!r}, category={self.spec.category!r})"


__all__ = ["FrontendScaffoldSpec", "ScaffoldFrontend"]
