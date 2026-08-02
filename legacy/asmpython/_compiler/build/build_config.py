"""Project-local ``build.config.toml`` loading and CLI expansion."""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 bootstrap fallback
    tomllib = None  # type: ignore[assignment]


CONFIG_NAME = "build.config.toml"


class BuildConfigError(RuntimeError):
    pass


def find_build_config(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    while True:
        candidate = current / CONFIG_NAME
        if candidate.is_file():
            return candidate
        if current.parent == current:
            return None
        current = current.parent


def load_build_config(path: Path) -> dict[str, Any]:
    if tomllib is None:
        raise BuildConfigError("build.config.toml requires Python 3.11+ tomllib support")
    try:
        with path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, ValueError) as exc:
        raise BuildConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise BuildConfigError(f"{path} must contain a TOML table")
    build = document.get("build", {})
    if not isinstance(build, dict):
        raise BuildConfigError(f"{path}: [build] must be a table")
    embed = document.get("embed", {})
    if embed and not isinstance(embed, dict):
        raise BuildConfigError(f"{path}: [embed] must be a table")
    return document


def _list(value: Any, *, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BuildConfigError(f"{key} must be a string or list of strings")
    return list(value)


def _target_args(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return ["--target", value]
    if isinstance(value, list) and len(value) == 3 and all(isinstance(item, str) for item in value):
        return ["--target", *value]
    raise BuildConfigError("build.target must be a string or a three-string array")


def _expand_embed(root: Path, include: list[str], exclude: list[str]) -> list[str]:
    matches: dict[str, Path] = {}
    excluded: set[Path] = set()
    for pattern in exclude:
        for raw in glob.glob(str(root / pattern), recursive=True):
            path = Path(raw)
            if path.is_file():
                excluded.add(path.resolve())
    for pattern in include:
        candidates = glob.glob(str(root / pattern), recursive=True)
        if not candidates and (root / pattern).is_file():
            candidates = [str(root / pattern)]
        for raw in candidates:
            path = Path(raw)
            if not path.is_file() or path.resolve() in excluded:
                continue
            relative = path.resolve().relative_to(root.resolve()).as_posix()
            matches[relative] = path.resolve()
    return [str(matches[name]) for name in sorted(matches)]


def config_to_argv(document: dict[str, Any], *, path: Path) -> list[str]:
    build = document.get("build", {})
    embed = document.get("embed", {}) or {}
    root = path.parent.resolve()
    result: list[str] = []

    entry = build.get("entry")
    if entry is not None:
        if not isinstance(entry, str):
            raise BuildConfigError("build.entry must be a string")
        result.append(str((root / entry).resolve()))

    scalar_flags = {
        "output": "--output",
        "type": "--type",
        "backend": "--backend",
        "linker": "--linker",
        "profile": "--profile",
        "report": "--report",
        "debug_format": "--debug-format",
        "lockfile": "--lockfile",
    }
    for key, flag in scalar_flags.items():
        value = build.get(key)
        if value is not None:
            if not isinstance(value, (str, int, float)):
                raise BuildConfigError(f"build.{key} must be scalar")
            text = str(value)
            if key in {"output", "report", "lockfile"}:
                text = str((root / text).resolve())
            result.extend((flag, text))

    result.extend(_target_args(build.get("target")))

    boolean_flags = {
        "fastcomp": "--fastcomp",
        "graphonly": "--graphonly",
        "debug": "--debug",
        "speedy_lossy": "--speedy-lossy",
        "bleach": "--bleach",
        "locked": "--locked",
    }
    for key, flag in boolean_flags.items():
        value = build.get(key)
        if value is True:
            result.append(flag)
        elif value not in (None, False):
            raise BuildConfigError(f"build.{key} must be true or false")

    for sanitizer in _list(build.get("sanitize"), key="build.sanitize"):
        result.extend(("--sanitize", sanitizer))

    include = _list(embed.get("include", build.get("embed")), key="embed.include")
    exclude = _list(embed.get("exclude"), key="embed.exclude")
    for file_path in _expand_embed(root, include, exclude):
        result.extend(("--embed", file_path))
    return result


def _extract_config_path(argv: list[str]) -> tuple[list[str], Path | None, bool]:
    remaining: list[str] = []
    selected: Path | None = None
    disabled = False
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--no-config":
            disabled = True
            index += 1
            continue
        if token == "--config":
            if index + 1 >= len(argv):
                raise BuildConfigError("--config requires a path")
            selected = Path(argv[index + 1])
            index += 2
            continue
        if token.startswith("--config="):
            selected = Path(token.split("=", 1)[1])
            index += 1
            continue
        remaining.append(token)
        index += 1
    return remaining, selected, disabled


def apply_build_config(argv: list[str], *, is_build: bool) -> tuple[list[str], Path | None]:
    if not is_build:
        return list(argv), None
    remaining, explicit, disabled = _extract_config_path(argv)
    if disabled:
        return remaining, None
    path = explicit.expanduser().resolve() if explicit is not None else find_build_config()
    if path is None:
        return remaining, None
    if not path.is_file():
        raise BuildConfigError(f"build config not found: {path}")
    injected = config_to_argv(load_build_config(path), path=path)

    # Config values are inserted before explicit CLI values, preserving the
    # ordinary "last occurrence wins" behavior of argparse and the facade.
    if remaining and remaining[0] == "build":
        explicit_tail = remaining[1:]
        config_has_entry = bool(injected and not injected[0].startswith("-"))
        explicit_has_source = bool(explicit_tail and not explicit_tail[0].startswith("-"))
        if config_has_entry and explicit_has_source:
            injected = injected[1:]
        return ["build", *injected, *explicit_tail], path
    return [*injected, *remaining], path


__all__ = [
    "BuildConfigError",
    "CONFIG_NAME",
    "apply_build_config",
    "config_to_argv",
    "find_build_config",
    "load_build_config",
]
