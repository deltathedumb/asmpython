"""The frontend interface.

A frontend turns source text into a verified `Module`. Mirror image of a
backend, and the same size:

    class MyLang(Frontend):
        name = "mylang"
        extensions = (".ml",)
        def compile(self, source, sink) -> Module | None

Everything language-specific lives on this side of the line. The IR has no
opinion about Python's `//` flooring toward negative infinity, about boxing, or
about dynamic dispatch: those are things a frontend LOWERS into the IR's small
vocabulary plus whatever runtime functions it decides to call.

`Op.DIV` truncates toward zero, like C and like every machine. Python's `//`
floors. So a Python frontend emits more than one instruction for `//` -- paid
once here, rather than owed by each of the backends. That is the whole argument
for a small IR, and it is why this interface takes source text and returns IR
with nothing in between that a backend could reach into.

Returning None means "I reported errors to the sink". A frontend never raises
for bad user input; it reports and returns None so the driver can decide
whether to continue with other inputs.
"""
from __future__ import annotations

import abc
from pathlib import Path

from ..diagnostics import DiagnosticSink, SourceFile
from ..ir import Module


class Frontend(abc.ABC):
    """Turn source text into a Module."""

    name: str = ""
    extensions: tuple[str, ...] = ()
    description: str = ""

    @abc.abstractmethod
    def compile(self, source: SourceFile, sink: DiagnosticSink) -> Module | None:
        """Lower `source` to a Module, or return None having reported errors."""

    def __repr__(self) -> str:
        return f"<frontend {self.name}>"


_REGISTRY: dict[str, Frontend] = {}


def register(fe: Frontend) -> Frontend:
    if not fe.name:
        raise ValueError(f"{type(fe).__name__} has no name")
    if fe.name in _REGISTRY:
        raise ValueError(f"frontend {fe.name!r} is already registered")
    _REGISTRY[fe.name] = fe
    return fe


def get(name: str) -> Frontend:
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise SystemExit(f"unknown frontend {name!r}\navailable: {known}") from None


def for_path(path: Path) -> Frontend | None:
    matches = [f for f in _REGISTRY.values() if path.suffix in f.extensions]
    return matches[0] if len(matches) == 1 else None


def available() -> dict[str, Frontend]:
    return dict(_REGISTRY)


def load_builtin() -> None:
    from ..frontends import python  # noqa: F401
