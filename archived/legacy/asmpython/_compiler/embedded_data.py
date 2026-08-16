"""Deterministic resources appended after native executable data.

Native loaders ignore trailing bytes. ASMPython appends a payload followed by a
small footer so the running program can locate the resource tree without
changing PE, ELF, or Mach-O headers.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MAGIC = b"ASMPYEMB1"
FORMAT_VERSION = 1
_FOOTER = struct.Struct("<9sQQ32s")


class EmbeddedDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class EmbeddedEntry:
    name: str
    offset: int
    size: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "offset": self.offset,
            "size": self.size,
            "sha256": self.sha256,
        }


def _safe_name(name: str) -> str:
    normalized = name.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise EmbeddedDataError(f"unsafe embedded path {name!r}")
    return "/".join(parts)


def collect_files(paths: Iterable[Path], *, root: Path | None = None) -> dict[str, bytes]:
    root = (root or Path.cwd()).resolve()
    result: dict[str, bytes] = {}
    for raw in paths:
        path = raw.expanduser().resolve()
        if not path.is_file():
            raise EmbeddedDataError(f"embedded file does not exist: {raw}")
        try:
            name = path.relative_to(root).as_posix()
        except ValueError:
            name = path.name
        name = _safe_name(name)
        if name in result:
            raise EmbeddedDataError(f"duplicate embedded path {name!r}")
        result[name] = path.read_bytes()
    return dict(sorted(result.items()))


def encode_resources(files: dict[str, bytes]) -> bytes:
    data = bytearray()
    entries: list[EmbeddedEntry] = []
    for name, content in sorted(files.items()):
        safe = _safe_name(name)
        entries.append(
            EmbeddedEntry(
                name=safe,
                offset=len(data),
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
        data.extend(content)
    manifest = json.dumps(
        {
            "format": "asmpython.embedded",
            "format_version": FORMAT_VERSION,
            "files": [entry.as_dict() for entry in entries],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    payload = struct.pack("<Q", len(manifest)) + manifest + bytes(data)
    digest = hashlib.sha256(payload).digest()
    return payload + _FOOTER.pack(MAGIC, len(payload), len(entries), digest)


def decode_resources(blob: bytes) -> dict[str, bytes]:
    if len(blob) < _FOOTER.size:
        return {}
    magic, payload_size, count, expected = _FOOTER.unpack(blob[-_FOOTER.size :])
    if magic != MAGIC:
        return {}
    start = len(blob) - _FOOTER.size - payload_size
    if start < 0:
        raise EmbeddedDataError("embedded footer points before the start of the file")
    payload = blob[start : len(blob) - _FOOTER.size]
    if hashlib.sha256(payload).digest() != expected:
        raise EmbeddedDataError("embedded payload SHA-256 mismatch")
    if len(payload) < 8:
        raise EmbeddedDataError("embedded payload is truncated")
    manifest_size = struct.unpack("<Q", payload[:8])[0]
    manifest_end = 8 + manifest_size
    try:
        manifest = json.loads(payload[8:manifest_end].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise EmbeddedDataError(f"malformed embedded manifest: {exc}") from exc
    if manifest.get("format") != "asmpython.embedded" or manifest.get("format_version") != FORMAT_VERSION:
        raise EmbeddedDataError("unsupported embedded resource format/version")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) != count:
        raise EmbeddedDataError("embedded resource count mismatch")
    raw_data = payload[manifest_end:]
    result: dict[str, bytes] = {}
    for item in entries:
        name = _safe_name(str(item["name"]))
        offset = int(item["offset"])
        size = int(item["size"])
        content = raw_data[offset : offset + size]
        if len(content) != size:
            raise EmbeddedDataError(f"embedded resource {name!r} is truncated")
        digest = hashlib.sha256(content).hexdigest()
        if digest != item["sha256"]:
            raise EmbeddedDataError(f"embedded resource {name!r} failed SHA-256 verification")
        result[name] = content
    return result


def read_resources(path: Path) -> dict[str, bytes]:
    return decode_resources(path.read_bytes())


def strip_resources(blob: bytes) -> bytes:
    if len(blob) < _FOOTER.size:
        return blob
    magic, payload_size, _count, _digest = _FOOTER.unpack(blob[-_FOOTER.size :])
    if magic != MAGIC:
        return blob
    start = len(blob) - _FOOTER.size - payload_size
    if start < 0:
        raise EmbeddedDataError("invalid embedded payload length")
    return blob[:start]


def append_resources(path: Path, files: dict[str, bytes]) -> None:
    original = strip_resources(path.read_bytes())
    rendered = original + encode_resources(files)
    temporary = path.with_name(path.name + ".embed.tmp")
    temporary.write_bytes(rendered)
    try:
        mode = path.stat().st_mode
        os.chmod(temporary, mode)
    except OSError:
        pass
    temporary.replace(path)


def build_tree(files: dict[str, bytes]) -> dict[str, Any]:
    root: dict[str, Any] = {}
    for name, content in sorted(files.items()):
        parts = _safe_name(name).split("/")
        current = root
        for part in parts[:-1]:
            child = current.setdefault(part, {})
            if not isinstance(child, dict):
                raise EmbeddedDataError(f"embedded path collision at {part!r}")
            current = child
        if parts[-1] in current:
            raise EmbeddedDataError(f"embedded path collision at {name!r}")
        current[parts[-1]] = content
    return root


__all__ = [
    "EmbeddedDataError",
    "MAGIC",
    "append_resources",
    "build_tree",
    "collect_files",
    "decode_resources",
    "encode_resources",
    "read_resources",
    "strip_resources",
]
