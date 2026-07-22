"""Reproducible ASMPython build lockfiles and locked-build verification."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from asmpython._version import ASMPYTHON_VERSION, FULL_VERSION, PYTHON_LANGUAGE_VERSION
from .build_options import active_sanitizers, bleach_enabled, speedy_lossy_enabled
from .capability_negotiation import CapabilityNegotiationError, negotiate_build
from .extension_packages import list_installed


LOCK_FORMAT = "asmpython.build-lock"
LOCK_VERSION = 1
_DEFAULT_LOCK = Path("asmpython.lock")
_EXCLUDED = frozenset({
    ".git", ".hg", ".svn", ".asmpython", ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", "build", "dist",
})


class BuildLockError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files(source: Path | None) -> dict[str, str]:
    if source is None:
        return {}
    source = source.expanduser()
    if not source.exists():
        raise BuildLockError(f"source path does not exist: {source}")
    if source.is_file():
        return {source.name: _sha256(source)}
    result: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        if any(part in _EXCLUDED for part in relative.parts):
            continue
        result[relative.as_posix()] = _sha256(path)
    return result


def source_from_argv(argv: list[str]) -> Path | None:
    tokens = list(argv)
    if tokens and tokens[0] == "build":
        tokens = tokens[1:]
    skip_next = False
    value_flags = {
        "--backend", "--linker", "--target", "--type", "-o", "--output",
        "--profile", "--sanitize", "--report", "--lockfile", "--icon",
        "--nasm", "--gcc", "--apm",
    }
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token in value_flags:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return Path(token)
    return None


def _effective_extensions(directory: Path | None = None) -> list[dict[str, Any]]:
    chosen: dict[str, Any] = {}
    for item in list_installed(directory):
        chosen[item.id] = item
    return [
        {
            "id": item.id,
            "version": item.version,
            "scope": item.scope,
            "sha256": _sha256(item.path),
            "production_suitable": item.production_suitable,
        }
        for item in (chosen[key] for key in sorted(chosen))
    ]


def _component_record(result) -> dict[str, Any]:
    return {
        "kind": result.kind,
        "name": result.name,
        "capabilities": result.capabilities.as_dict(),
        "dependencies": [item.as_dict() for item in result.dependencies],
    }


def create_snapshot(argv: list[str], *, source: Path | None = None) -> dict[str, Any]:
    negotiation = negotiate_build(argv)
    if negotiation.errors:
        raise CapabilityNegotiationError("\n".join(negotiation.errors))
    source = source if source is not None else source_from_argv(argv)
    source_display = None
    if source is not None:
        try:
            source_display = source.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            source_display = str(source.resolve())
    return {
        "format": LOCK_FORMAT,
        "format_version": LOCK_VERSION,
        "asmpython": {
            "full_version": FULL_VERSION,
            "package_version": ASMPYTHON_VERSION,
            "python_language_version": PYTHON_LANGUAGE_VERSION,
        },
        "build": {
            "backend": negotiation.backend.name,
            "linker": None if negotiation.linker is None else negotiation.linker.name,
            "target": negotiation.target,
            "output_type": negotiation.output_type,
            "speedy_lossy": speedy_lossy_enabled(),
            "bleach": bleach_enabled(),
            "sanitizers": list(active_sanitizers()),
        },
        "components": {
            "backend": _component_record(negotiation.backend),
            "linker": None if negotiation.linker is None else _component_record(negotiation.linker),
        },
        "extensions": _effective_extensions(),
        "source": {
            "root": source_display,
            "files": _source_files(source),
        },
    }


def write_lock(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_lock(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BuildLockError(f"cannot read build lock {path}: {exc}") from exc
    if payload.get("format") != LOCK_FORMAT or payload.get("format_version") != LOCK_VERSION:
        raise BuildLockError(f"unsupported build lock format/version in {path}")
    return payload


def _argv_from_locked_build(build: dict[str, Any], source: str | None) -> list[str]:
    argv = ["build"]
    if source:
        argv.append(source)
    for key, flag in (
        ("backend", "--backend"),
        ("linker", "--linker"),
        ("target", "--target"),
        ("output_type", "--type"),
    ):
        value = build.get(key)
        if value:
            argv.extend((flag, str(value)))
    return argv


def _compare(expected: Any, actual: Any, prefix: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        mismatches: list[str] = []
        for key in sorted(set(expected) | set(actual)):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in expected:
                mismatches.append(f"{path}: unexpected current value {actual[key]!r}")
            elif key not in actual:
                mismatches.append(f"{path}: missing current value")
            else:
                mismatches.extend(_compare(expected[key], actual[key], path))
        return mismatches
    if expected != actual:
        return [f"{prefix}: locked {expected!r}, current {actual!r}"]
    return []


def verify_lock(path: Path, *, argv: list[str] | None = None) -> list[str]:
    locked = read_lock(path)
    source_name = locked.get("source", {}).get("root")
    source = Path(source_name) if source_name else None
    if argv is None:
        argv = _argv_from_locked_build(locked.get("build", {}), source_name)
    current = create_snapshot(argv, source=source)
    return _compare(locked, current)


def enforce_locked_build(path: Path, argv: list[str]) -> None:
    if not path.exists():
        raise BuildLockError(
            f"locked build requested but {path} does not exist; run `asmpython lock create`"
        )
    mismatches = verify_lock(path, argv=argv)
    if mismatches:
        preview = "\n".join(f"  - {item}" for item in mismatches[:30])
        extra = "" if len(mismatches) <= 30 else f"\n  - ... {len(mismatches) - 30} more"
        raise BuildLockError(f"build lock mismatch:\n{preview}{extra}")


def _build_argv(args: argparse.Namespace) -> list[str]:
    argv = ["build"]
    if args.source is not None:
        argv.append(str(args.source))
    for value, flag in (
        (args.backend, "--backend"),
        (args.linker, "--linker"),
        (args.target, "--target"),
        (args.output_type, "--type"),
    ):
        if value is not None:
            argv.extend((flag, value))
    return argv


def command_main(argv: list[str]) -> int:
    raw = list(argv)
    if not raw or raw[0] not in {"create", "update", "verify", "show"}:
        raw.insert(0, "create")
    parser = argparse.ArgumentParser(prog="asmpython lock")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("create", "update"):
        item = sub.add_parser(action)
        item.add_argument("source", nargs="?", type=Path)
        item.add_argument("--file", type=Path, default=_DEFAULT_LOCK)
        item.add_argument("--backend", default=None)
        item.add_argument("--linker", default=None)
        item.add_argument("--target", default=None)
        item.add_argument("--type", dest="output_type", default=None)
    verify = sub.add_parser("verify")
    verify.add_argument("--file", type=Path, default=_DEFAULT_LOCK)
    verify.add_argument("--json", action="store_true")
    show = sub.add_parser("show")
    show.add_argument("--file", type=Path, default=_DEFAULT_LOCK)
    show.add_argument("--json", action="store_true")
    args = parser.parse_args(raw)
    try:
        if args.action in {"create", "update"}:
            payload = create_snapshot(_build_argv(args), source=args.source)
            write_lock(args.file, payload)
            print(f"asmpython: wrote build lock {args.file}")
            return 0
        payload = read_lock(args.file)
        if args.action == "show":
            if args.json:
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                build = payload["build"]
                print(f"lock: {args.file}")
                print(f"release: {payload['asmpython']['full_version']}")
                print(f"backend: {build['backend']}")
                print(f"linker: {build['linker'] or '-'}")
                print(f"target: {build['target']}")
                print(f"extensions: {len(payload['extensions'])}")
                print(f"source files: {len(payload['source']['files'])}")
            return 0
        mismatches = verify_lock(args.file)
        result = {"valid": not mismatches, "file": str(args.file), "mismatches": mismatches}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        elif mismatches:
            print(f"asmpython: build lock {args.file} is stale", file=sys.stderr)
            for item in mismatches:
                print(f"  {item}", file=sys.stderr)
        else:
            print(f"asmpython: build lock {args.file} is valid")
        return 0 if not mismatches else 1
    except (BuildLockError, CapabilityNegotiationError, OSError, ValueError) as exc:
        print(f"asmpython: lock: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "BuildLockError",
    "LOCK_FORMAT",
    "LOCK_VERSION",
    "command_main",
    "create_snapshot",
    "enforce_locked_build",
    "read_lock",
    "verify_lock",
    "write_lock",
]
