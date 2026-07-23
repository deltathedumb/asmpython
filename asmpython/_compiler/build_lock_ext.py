"""Extended lockfile snapshot fields for modern build policies."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from . import build_lock as _base
from .build_options import (
    active_debug_format,
    active_embed_paths,
    debug_enabled,
    fastcomp_enabled,
)


_original_create_snapshot = _base.create_snapshot


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_snapshot(
    argv: list[str], *, source: Path | None = None
) -> dict[str, Any]:
    payload = _original_create_snapshot(argv, source=source)
    build = payload.setdefault("build", {})
    build.update(
        {
            "fastcomp": fastcomp_enabled(),
            "debug": debug_enabled(),
            "debug_format": active_debug_format(),
            "target_triple": build.get("target"),
        }
    )
    resources: list[dict[str, Any]] = []
    for raw in active_embed_paths():
        path = raw.expanduser().resolve()
        if not path.is_file():
            raise _base.BuildLockError(f"embedded file does not exist: {raw}")
        resources.append(
            {
                "path": str(path),
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    payload["embedded"] = resources
    return payload


# Existing verification helpers resolve create_snapshot through module globals,
# so replacing it extends create/update/verify and --locked consistently.
_base.create_snapshot = create_snapshot


__all__ = ["create_snapshot"]
