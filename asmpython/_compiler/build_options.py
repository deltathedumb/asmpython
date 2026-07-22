"""Build-wide options that must reach every backend and linker.

The public CLI strips shared flags before delegating to the historical parser,
then installs them in this context. Backend/linker adapters inject a concrete
copy into every argument dictionary, including ``False`` when the mode is off,
so plugins never need to infer whether the option was omitted.
"""
from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Iterator
from typing import Any


_speedy_lossy: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "asmpython_speedy_lossy", default=False
)


def speedy_lossy_enabled() -> bool:
    """Return whether the active build permits faster, lower-quality output."""

    return bool(_speedy_lossy.get())


@contextlib.contextmanager
def speedy_lossy_mode(enabled: bool) -> Iterator[None]:
    """Install the build mode for backend/linker calls in this context."""

    token = _speedy_lossy.set(bool(enabled))
    try:
        yield
    finally:
        _speedy_lossy.reset(token)


def inject_build_options(args: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copied plugin argument dictionary with shared build options.

    The shared value deliberately overrides an incoming value. The CLI/build
    context is authoritative, preventing a backend-local default or stale cached
    dictionary from silently changing the selected mode.
    """

    resolved = dict(args or {})
    resolved["speedy_lossy"] = speedy_lossy_enabled()
    return resolved


def extract_speedy_lossy(argv: list[str]) -> tuple[list[str], bool]:
    """Remove repeated ``--speedy-lossy`` flags from an argv vector."""

    remaining: list[str] = []
    enabled = False
    for token in argv:
        if token == "--speedy-lossy":
            enabled = True
            continue
        if token.startswith("--speedy-lossy="):
            raise ValueError("--speedy-lossy is a flag and does not accept a value")
        remaining.append(token)
    return remaining, enabled


__all__ = [
    "extract_speedy_lossy",
    "inject_build_options",
    "speedy_lossy_enabled",
    "speedy_lossy_mode",
]
