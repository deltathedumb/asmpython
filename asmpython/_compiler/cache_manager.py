"""Unified ASMPython cache inspection and maintenance."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CacheError(RuntimeError):
    pass


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    size: int
    modified: float
    manifest: dict[str, Any] | None
    valid: bool
    error: str | None = None


def default_cache_dir() -> Path:
    override = os.environ.get("ASMPYTHON_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "ASMPython" / "cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "asmpython"


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _latest_mtime(path: Path) -> float:
    latest = 0.0
    try:
        latest = path.stat().st_mtime
        for child in path.rglob("*"):
            try:
                latest = max(latest, child.stat().st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return latest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        return None, None
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"invalid manifest: {exc}"
    if not isinstance(value, dict):
        return None, "manifest root is not an object"
    return value, None


def _verify_manifest_files(entry: Path, manifest: dict[str, Any]) -> str | None:
    hashes = manifest.get("files", manifest.get("artifacts"))
    if hashes is None:
        return None
    if not isinstance(hashes, dict):
        return "manifest files/artifacts field is not an object"
    for relative, expected in hashes.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            return "manifest hash entries must be string-to-string"
        candidate = (entry / relative).resolve()
        try:
            candidate.relative_to(entry.resolve())
        except ValueError:
            return f"manifest path escapes cache entry: {relative}"
        if not candidate.is_file():
            return f"missing cached file: {relative}"
        if _sha256(candidate) != expected.lower():
            return f"hash mismatch: {relative}"
    return None


def scan_cache(root: Path | None = None) -> list[CacheEntry]:
    root = default_cache_dir() if root is None else Path(root)
    if not root.exists():
        return []
    if not root.is_dir():
        raise CacheError(f"cache path is not a directory: {root}")
    entries: list[CacheEntry] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        manifest, error = _load_manifest(child)
        if error is None and manifest is not None:
            error = _verify_manifest_files(child, manifest)
        entries.append(
            CacheEntry(
                path=child,
                size=_directory_size(child),
                modified=_latest_mtime(child),
                manifest=manifest,
                valid=error is None,
                error=error,
            )
        )
    return entries


def clear_cache(
    root: Path | None = None,
    *,
    source: Path | None = None,
    key: str | None = None,
) -> int:
    root = default_cache_dir() if root is None else Path(root)
    if not root.exists():
        return 0
    if source is None and key is None:
        count = len([item for item in root.iterdir() if item.is_dir()])
        shutil.rmtree(root)
        return count

    source_value = str(source.resolve()) if source is not None else None
    removed = 0
    for entry in scan_cache(root):
        if key is not None and entry.path.name != key:
            continue
        if source_value is not None:
            manifest = entry.manifest or {}
            recorded = manifest.get("source_path")
            dependencies = manifest.get("dependencies", {})
            if recorded != source_value and source_value not in dependencies:
                continue
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    if root.exists() and not any(root.iterdir()):
        try:
            root.rmdir()
        except OSError:
            pass
    return removed


def verify_cache(root: Path | None = None, *, repair: bool = False) -> tuple[int, int]:
    valid = invalid = 0
    for entry in scan_cache(root):
        if entry.valid:
            valid += 1
            continue
        invalid += 1
        if repair:
            shutil.rmtree(entry.path, ignore_errors=True)
    return valid, invalid


def prune_cache(
    root: Path | None = None,
    *,
    max_age_days: float | None = None,
    max_bytes: int | None = None,
) -> tuple[int, int]:
    root = default_cache_dir() if root is None else Path(root)
    entries = scan_cache(root)
    removed = 0
    reclaimed = 0
    now = time.time()

    for entry in list(entries):
        expired = (
            max_age_days is not None
            and entry.modified > 0
            and now - entry.modified > max_age_days * 86400
        )
        if expired or not entry.valid:
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
            reclaimed += entry.size
            entries.remove(entry)

    if max_bytes is not None:
        remaining = sum(entry.size for entry in entries)
        for entry in sorted(entries, key=lambda item: item.modified):
            if remaining <= max_bytes:
                break
            shutil.rmtree(entry.path, ignore_errors=True)
            removed += 1
            reclaimed += entry.size
            remaining -= entry.size
    return removed, reclaimed


def format_size(value: int) -> str:
    amount = float(value)
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or suffix == "TiB":
            return f"{amount:.1f} {suffix}" if suffix != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"
