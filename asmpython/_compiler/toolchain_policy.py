"""Production-suitability metadata and warnings for backends and linkers."""
from __future__ import annotations

import sys
from typing import Any


_BUILTIN_BACKENDS: dict[str, bool] = {
    "legacy": True,
    "x86-64": True,
    "x64": True,
    "amd64": True,
    "ternary": False,
}
_BUILTIN_LINKERS: dict[str, bool] = {
    "gcc": True,
    "builtin": True,
}


def _option_value(argv: list[str], name: str) -> str | None:
    prefix = name + "="
    for index, token in enumerate(argv):
        if token == name and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith(prefix):
            return token[len(prefix):]
    return None


def backend_production_suitable(name: str) -> bool | None:
    """Return True/False for known backends, or None when unknown."""

    if name in _BUILTIN_BACKENDS:
        return _BUILTIN_BACKENDS[name]
    try:
        from asmpython._backends import get_backend

        backend = get_backend(name)
    except Exception:
        return None
    if backend is None:
        return None
    return bool(getattr(backend, "production_suitable", True))


def linker_production_suitable(name: str) -> bool | None:
    """Return True/False for known linkers, or None when unknown."""

    if name in _BUILTIN_LINKERS:
        return _BUILTIN_LINKERS[name]
    try:
        from asmpython._linkers import get_linker

        linker = get_linker(name)
    except Exception:
        return None
    if linker is None:
        return None
    return bool(getattr(linker, "production_suitable", True))


def component_status(kind: str, name: str) -> dict[str, Any]:
    """Return machine-readable status used by CLI/dev tooling."""

    if kind == "backend":
        suitable = backend_production_suitable(name)
    elif kind == "linker":
        suitable = linker_production_suitable(name)
    else:
        raise ValueError(f"unknown toolchain component kind {kind!r}")
    return {
        "kind": kind,
        "name": name,
        "known": suitable is not None,
        "production_suitable": suitable,
    }


def warn_selected_nonproduction(argv: list[str]) -> None:
    """Warn when an explicitly selected component is marked non-production."""

    backend = _option_value(argv, "--backend")
    linker = _option_value(argv, "--linker")
    if backend and backend_production_suitable(backend) is False:
        print(
            f"asmpython: warning: backend {backend!r} is marked unsuitable for "
            "production builds",
            file=sys.stderr,
        )
    if linker and linker_production_suitable(linker) is False:
        print(
            f"asmpython: warning: linker {linker!r} is marked unsuitable for "
            "production builds",
            file=sys.stderr,
        )


__all__ = [
    "backend_production_suitable",
    "component_status",
    "linker_production_suitable",
    "warn_selected_nonproduction",
]
