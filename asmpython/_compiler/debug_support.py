"""Debugger metadata selection and ASMPython mixed-frame sidecars."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


VALID_DEBUG_FORMATS = frozenset({"auto", "dwarf", "pdb", "codeview", "sourcemap"})


class DebugSupportError(RuntimeError):
    pass


def default_debug_format(target: str | None) -> str:
    value = (target or "").lower()
    if "windows" in value or value in {"win", "win32", "win64"}:
        return "pdb"
    if "wasm" in value or "web" in value:
        return "sourcemap"
    return "dwarf"


def infer_output_path(argv: list[str]) -> Path | None:
    output: str | None = None
    source: Path | None = None
    target: str | None = None
    output_type = "executable"
    index = 1 if argv and argv[0] == "build" else 0
    value_flags = {
        "--backend", "--linker", "--target", "--type", "--output", "-o",
        "--profile", "--sanitize", "--report", "--debug-format", "--embed",
        "--lockfile", "--config", "--graph-format", "--graph-output",
    }
    while index < len(argv):
        token = argv[index]
        if token in {"--output", "-o"} and index + 1 < len(argv):
            output = argv[index + 1]
            index += 2
            continue
        if token.startswith("--output="):
            output = token.split("=", 1)[1]
        elif token == "--target" and index + 1 < len(argv):
            target = argv[index + 1]
            index += 2
            continue
        elif token == "--type" and index + 1 < len(argv):
            output_type = argv[index + 1]
            index += 2
            continue
        elif token in value_flags:
            index += 2
            continue
        elif not token.startswith("-") and source is None:
            source = Path(token)
        index += 1
    if output:
        return Path(output)
    if source is None:
        return None
    suffix = source.suffix.lower()
    if suffix not in {".py", ".apir", ".json", ".toml"}:
        return None
    stem = source.stem
    target_lower = (target or "").lower()
    if output_type in {"shared-library", "shared", "library"}:
        if "windows" in target_lower or sys.platform == "win32":
            return Path(stem + ".dll")
        if "mac" in target_lower or "darwin" in target_lower:
            return Path("lib" + stem + ".dylib")
        return Path("lib" + stem + ".so")
    if "windows" in target_lower or (not target and sys.platform == "win32"):
        return Path(stem + ".exe")
    return Path(stem)


def write_debug_sidecar(
    artifact: Path,
    *,
    source: Path | None,
    target: str | None,
    backend: str | None,
    linker: str | None,
    debug_format: str,
    mixed_tracebacks: bool = True,
) -> Path:
    if debug_format == "auto":
        debug_format = default_debug_format(target)
    if debug_format not in VALID_DEBUG_FORMATS:
        raise DebugSupportError(
            "debug format must be one of " + ", ".join(sorted(VALID_DEBUG_FORMATS))
        )
    payload: dict[str, Any] = {
        "format": "asmpython.debug-map",
        "format_version": 1,
        "artifact": str(artifact),
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "source": None if source is None else str(source.resolve()),
        "target": target,
        "backend": backend,
        "linker": linker,
        "native_debug_format": debug_format,
        "mixed_native_pyinbin_frames": bool(mixed_tracebacks),
        "symbol_map": [],
        "source_map": [],
        "notes": [
            "Backends populate symbol_map/source_map when they emit concrete variable and line locations.",
            "This sidecar preserves ASMPython/PyinBin frame identity for debugger adapters.",
        ],
    }
    output = artifact.with_suffix(artifact.suffix + ".asmpdebug.json")
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return output


__all__ = [
    "DebugSupportError",
    "VALID_DEBUG_FORMATS",
    "default_debug_format",
    "infer_output_path",
    "write_debug_sidecar",
]
