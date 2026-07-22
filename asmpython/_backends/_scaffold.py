"""Hard-fail scaffolding for planned ASMPython backends.

A scaffold is intentionally discoverable through ``--backend`` while making no
compatibility or code-generation claim. Every executable operation raises
``NotImplementedError`` until a real backend replaces the scaffold.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from .._compiler.ir import IRBackend, IRModule


@dataclass(frozen=True)
class BackendScaffoldSpec:
    """Static identity and planned CLI surface for one future backend."""

    name: str
    display_name: str
    category: str
    aliases: tuple[str, ...] = ()
    planned_parameters: tuple[str, ...] = ()
    notes: str = ""


class ScaffoldBackend(IRBackend):
    """An importable backend placeholder that can never emit an artifact."""

    is_scaffold = True
    implemented = False

    def __init__(self, spec: BackendScaffoldSpec) -> None:
        self.spec = spec
        self.name = spec.name

    @property
    def requested_args(self) -> list[dict]:
        # Planned parameters are metadata only until the backend has a real
        # implementation and argument-parser integration.
        return []

    @property
    def default_linker(self) -> str:
        # A scaffold must never imply that a real linker path exists.
        return "none"

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.spec.aliases

    @property
    def planned_parameters(self) -> tuple[str, ...]:
        return self.spec.planned_parameters

    def unavailable(self, operation: str) -> NoReturn:
        raise NotImplementedError(
            f"ASMPython backend {self.name!r} ({self.spec.display_name}) is "
            f"scaffolded only; {operation} is not implemented"
        )

    def compile(self, module: IRModule, args: dict) -> dict[str, bytes]:
        self.unavailable("IR compilation")

    def link(self, objects: list[bytes], args: dict) -> dict[str, bytes]:
        self.unavailable("linking or packaging")

    def validate_ir(self, module: IRModule, args: dict | None = None) -> None:
        self.unavailable("IR validation")

    def emit_object(self, module: IRModule, args: dict | None = None) -> bytes:
        self.unavailable("object emission")

    def emit_source(self, module: IRModule, args: dict | None = None) -> str:
        self.unavailable("source emission")

    def package(self, files: dict[str, bytes], args: dict | None = None) -> dict[str, bytes]:
        self.unavailable("artifact packaging")

    def __repr__(self) -> str:
        return f"ScaffoldBackend(name={self.name!r}, category={self.spec.category!r})"


__all__ = ["BackendScaffoldSpec", "ScaffoldBackend"]
