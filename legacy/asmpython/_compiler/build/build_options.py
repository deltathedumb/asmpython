"""Build-wide options that must reach every backend and linker.

The public CLI strips shared flags before delegating to the historical parser,
then installs them in context variables. Backend/linker adapters inject a
concrete copy into every argument dictionary, including false/empty values.
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
VALID_DEBUG_FORMATS = frozenset({"auto", "dwarf", "pdb", "codeview", "sourcemap"})
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
_fastcomp: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "asmpython_fastcomp", default=False
)
_debug: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "asmpython_debug", default=False
)
_debug_format: contextvars.ContextVar[str] = contextvars.ContextVar(
    "asmpython_debug_format", default="auto"
)
_embed_paths: contextvars.ContextVar[tuple[Path, ...]] = contextvars.ContextVar(
    "asmpython_embed_paths", default=()
)


@dataclass(frozen=True)
class SharedBuildOptions:
    speedy_lossy: bool = False
    bleach: bool = False
    sanitizers: tuple[str, ...] = ()
    report_path: Path | None = None
    locked: bool = False
    lockfile_path: Path = Path("asmpython.lock")
    fastcomp: bool = False
    debug: bool = False
    debug_format: str = "auto"
    embed_paths: tuple[Path, ...] = ()


def speedy_lossy_enabled() -> bool:
    return bool(_speedy_lossy.get())


def bleach_enabled() -> bool:
    return bool(_bleach.get())


def active_sanitizers() -> tuple[str, ...]:
    return tuple(_sanitizers.get())


def fastcomp_enabled() -> bool:
    return bool(_fastcomp.get())


def debug_enabled() -> bool:
    return bool(_debug.get())


def active_debug_format() -> str:
    return str(_debug_format.get())


def active_embed_paths() -> tuple[Path, ...]:
    return tuple(_embed_paths.get())


def _set_environment(options: SharedBuildOptions) -> dict[str, str | None]:
    names = {
        "ASMPYTHON_SPEEDY_LOSSY": "1" if options.speedy_lossy else "0",
        "ASMPYTHON_BLEACH": "1" if options.bleach else "0",
        "ASMPYTHON_SANITIZERS": ",".join(options.sanitizers),
        "ASMPYTHON_FASTCOMP": "1" if options.fastcomp else "0",
        "ASMPYTHON_DEBUG": "1" if options.debug else "0",
        "ASMPYTHON_DEBUG_FORMAT": options.debug_format,
        "ASMPYTHON_EMBED_PATHS": os.pathsep.join(str(path) for path in options.embed_paths),
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
    current = SharedBuildOptions(
        speedy_lossy=bool(enabled),
        bleach=bleach_enabled(),
        sanitizers=active_sanitizers(),
        fastcomp=fastcomp_enabled(),
        debug=debug_enabled(),
        debug_format=active_debug_format(),
        embed_paths=active_embed_paths(),
    )
    with shared_build_options(current):
        yield


@contextlib.contextmanager
def shared_build_options(options: SharedBuildOptions) -> Iterator[None]:
    tokens = (
        (_speedy_lossy, _speedy_lossy.set(bool(options.speedy_lossy))),
        (_bleach, _bleach.set(bool(options.bleach))),
        (_sanitizers, _sanitizers.set(tuple(options.sanitizers))),
        (_fastcomp, _fastcomp.set(bool(options.fastcomp))),
        (_debug, _debug.set(bool(options.debug))),
        (_debug_format, _debug_format.set(str(options.debug_format))),
        (_embed_paths, _embed_paths.set(tuple(options.embed_paths))),
    )
    previous = _set_environment(options)
    try:
        yield
    finally:
        _restore_environment(previous)
        for variable, token in reversed(tokens):
            variable.reset(token)


def inject_build_options(args: dict[str, Any] | None) -> dict[str, Any]:
    """Copy plugin arguments and inject all authoritative shared options."""
    resolved = dict(args or {})
    resolved["speedy_lossy"] = speedy_lossy_enabled()
    resolved["bleach"] = bleach_enabled()
    resolved["sanitizers"] = active_sanitizers()
    resolved["fastcomp"] = fastcomp_enabled()
    resolved["debug"] = debug_enabled()
    resolved["debug_format"] = active_debug_format()
    resolved["embed_paths"] = active_embed_paths()
    return resolved


def extract_speedy_lossy(argv: list[str]) -> tuple[list[str], bool]:
    remaining, options = extract_shared_build_options(argv)
    return remaining, options.speedy_lossy


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
    locked = False
    lockfile_path = Path("asmpython.lock")
    fastcomp = False
    debug = False
    debug_format = "auto"
    embed_paths: list[Path] = []
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
        if token == "--locked":
            locked = True
            index += 1
            continue
        if token.startswith("--locked="):
            raise ValueError("--locked is a flag and does not accept a value")
        if token == "--lockfile":
            if index + 1 >= len(argv):
                raise ValueError("--lockfile requires a path")
            lockfile_path = Path(argv[index + 1])
            index += 2
            continue
        if token.startswith("--lockfile="):
            value = token.split("=", 1)[1]
            if not value:
                raise ValueError("--lockfile requires a path")
            lockfile_path = Path(value)
            index += 1
            continue
        if token == "--fastcomp":
            fastcomp = True
            index += 1
            continue
        if token.startswith("--fastcomp="):
            raise ValueError("--fastcomp is a flag and does not accept a value")
        if token == "--debug":
            debug = True
            index += 1
            continue
        if token.startswith("--debug="):
            raise ValueError("--debug is a flag and does not accept a value")
        if token == "--debug-format":
            if index + 1 >= len(argv):
                raise ValueError("--debug-format requires a value")
            debug_format = argv[index + 1].lower()
            index += 2
            continue
        if token.startswith("--debug-format="):
            debug_format = token.split("=", 1)[1].lower()
            index += 1
            continue
        if token == "--embed":
            if index + 1 >= len(argv):
                raise ValueError("--embed requires a file path")
            embed_paths.append(Path(argv[index + 1]))
            index += 2
            continue
        if token.startswith("--embed="):
            value = token.split("=", 1)[1]
            if not value:
                raise ValueError("--embed requires a file path")
            embed_paths.append(Path(value))
            index += 1
            continue
        remaining.append(token)
        index += 1
    _validate_sanitizers(selected)
    if debug_format not in VALID_DEBUG_FORMATS:
        raise ValueError(
            "--debug-format must be one of " + ", ".join(sorted(VALID_DEBUG_FORMATS))
        )
    if debug_format != "auto":
        debug = True
    return remaining, SharedBuildOptions(
        speedy_lossy=speedy_lossy,
        bleach=bleach,
        sanitizers=tuple(sorted(selected)),
        report_path=report_path,
        locked=locked,
        lockfile_path=lockfile_path,
        fastcomp=fastcomp,
        debug=debug,
        debug_format=debug_format,
        embed_paths=tuple(embed_paths),
    )


__all__ = [
    "BLEACH_SANITIZERS",
    "SharedBuildOptions",
    "VALID_DEBUG_FORMATS",
    "VALID_SANITIZERS",
    "active_debug_format",
    "active_embed_paths",
    "active_sanitizers",
    "bleach_enabled",
    "debug_enabled",
    "extract_shared_build_options",
    "extract_speedy_lossy",
    "fastcomp_enabled",
    "inject_build_options",
    "shared_build_options",
    "speedy_lossy_enabled",
    "speedy_lossy_mode",
]
