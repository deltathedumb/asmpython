"""Build-wide options that must reach every backend and linker.

The public CLI strips shared flags before delegating to the historical parser,
then installs them in context variables. Backend/linker adapters inject a
concrete copy into every argument dictionary, including false/empty values, so
plugins never need to infer whether an option was omitted. Subprocess-based and
legacy toolchains receive equivalent ``ASMPYTHON_*`` environment variables.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VALID_SANITIZERS = frozenset({
    "address", "bounds", "integer", "leak", "memory", "thread", "undefined",
})
BLEACH_SANITIZERS = ("address", "leak", "undefined")

_speedy_lossy: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "asmpython_speedy_lossy", default=False
)
_bleach: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "asmpython_bleach", default=False
)
_sanitizers: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar(
    "asmpython_sanitizers", default=()
)


@dataclass(frozen=True)
class SharedBuildOptions:
    speedy_lossy: bool = False
    bleach: bool = False
    sanitizers: tuple[str, ...] = ()
    report_path: Path | None = None


def speedy_lossy_enabled() -> bool:
    return bool(_speedy_lossy.get())


def bleach_enabled() -> bool:
    return bool(_bleach.get())


def active_sanitizers() -> tuple[str, ...]:
    return tuple(_sanitizers.get())


def _set_environment(*, speedy_lossy: bool, bleach: bool,
                     sanitizers: tuple[str, ...]) -> dict[str, str | None]:
    names = {
        "ASMPYTHON_SPEEDY_LOSSY": "1" if speedy_lossy else "0",
        "ASMPYTHON_BLEACH": "1" if bleach else "0",
        "ASMPYTHON_SANITIZERS": ",".join(sanitizers),
    }
    previous = {name: os.environ.get(name) for name in names}
    os.environ.update(names)
    return previous


def _restore_environment(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@contextlib.contextmanager
def speedy_lossy_mode(enabled: bool) -> Iterator[None]:
    """Compatibility context manager for callers that set only this option."""

    value = bool(enabled)
    token = _speedy_lossy.set(value)
    previous = _set_environment(
        speedy_lossy=value, bleach=bleach_enabled(), sanitizers=active_sanitizers()
    )
    try:
        yield
    finally:
        _restore_environment(previous)
        _speedy_lossy.reset(token)


@contextlib.contextmanager
def shared_build_options(options: SharedBuildOptions) -> Iterator[None]:
    speedy_token = _speedy_lossy.set(bool(options.speedy_lossy))
    bleach_token = _bleach.set(bool(options.bleach))
    sanitizer_token = _sanitizers.set(tuple(options.sanitizers))
    previous = _set_environment(
        speedy_lossy=options.speedy_lossy,
        bleach=options.bleach,
        sanitizers=options.sanitizers,
    )
    try:
        yield
    finally:
        _restore_environment(previous)
        _sanitizers.reset(sanitizer_token)
        _bleach.reset(bleach_token)
        _speedy_lossy.reset(speedy_token)


def inject_build_options(args: dict[str, Any] | None) -> dict[str, Any]:
    """Copy plugin arguments and inject all authoritative shared options."""

    resolved = dict(args or {})
    resolved["speedy_lossy"] = speedy_lossy_enabled()
    resolved["bleach"] = bleach_enabled()
    resolved["sanitizers"] = active_sanitizers()
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


def _parse_sanitizer_value(value: str, selected: set[str]) -> None:
    for item in value.replace(",", " ").split():
        name = item.strip().lower()
        if name not in VALID_SANITIZERS:
            raise ValueError(
                f"unknown sanitizer {name!r}; choose from {', '.join(sorted(VALID_SANITIZERS))}"
            )
        selected.add(name)


def _validate_sanitizers(selected: set[str]) -> None:
    if "thread" in selected and selected.intersection({"address", "leak", "memory"}):
        raise ValueError(
            "the thread sanitizer cannot be combined with address, leak, or memory sanitizers"
        )
    if "memory" in selected and selected.intersection({"address", "leak"}):
        raise ValueError(
            "the memory sanitizer cannot be combined with address or leak sanitizers"
        )


def extract_shared_build_options(argv: list[str]) -> tuple[list[str], SharedBuildOptions]:
    """Remove shared build flags and return their normalized values."""

    remaining: list[str] = []
    speedy_lossy = False
    bleach = False
    selected: set[str] = set()
    report_path: Path | None = None
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--speedy-lossy":
            speedy_lossy = True
            index += 1
            continue
        if token.startswith("--speedy-lossy="):
            raise ValueError("--speedy-lossy is a flag and does not accept a value")
        if token == "--bleach":
            bleach = True
            selected.update(BLEACH_SANITIZERS)
            index += 1
            continue
        if token.startswith("--bleach="):
            raise ValueError("--bleach is a flag and does not accept a value")
        if token == "--sanitize":
            if index + 1 >= len(argv):
                raise ValueError("--sanitize requires a sanitizer name")
            _parse_sanitizer_value(argv[index + 1], selected)
            index += 2
            continue
        if token.startswith("--sanitize="):
            _parse_sanitizer_value(token.split("=", 1)[1], selected)
            index += 1
            continue
        if token == "--report":
            if index + 1 >= len(argv):
                raise ValueError("--report requires an output path")
            report_path = Path(argv[index + 1])
            index += 2
            continue
        if token.startswith("--report="):
            value = token.split("=", 1)[1]
            if not value:
                raise ValueError("--report requires an output path")
            report_path = Path(value)
            index += 1
            continue
        remaining.append(token)
        index += 1
    _validate_sanitizers(selected)
    return remaining, SharedBuildOptions(
        speedy_lossy=speedy_lossy,
        bleach=bleach,
        sanitizers=tuple(sorted(selected)),
        report_path=report_path,
    )


__all__ = [
    "BLEACH_SANITIZERS",
    "SharedBuildOptions",
    "VALID_SANITIZERS",
    "active_sanitizers",
    "bleach_enabled",
    "extract_shared_build_options",
    "extract_speedy_lossy",
    "inject_build_options",
    "shared_build_options",
    "speedy_lossy_enabled",
    "speedy_lossy_mode",
]
