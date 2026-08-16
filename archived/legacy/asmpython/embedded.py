"""Dictionary-like access to resources appended to the running binary.

Usage::

    from asmpython import embedded
    LICENSE = embedded["LICENSE"]

Leaf values are ``bytes``. Directories are nested dictionaries.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any


_tree: dict[str, Any] | None = None


def _source_path() -> Path:
    override = os.environ.get("ASMPYTHON_EMBEDDED_FILE")
    if override:
        return Path(override).expanduser()
    return Path(sys.executable)


def reload(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    global _tree
    from ._compiler.embedded_data import build_tree, read_resources

    selected = Path(path) if path is not None else _source_path()
    try:
        _tree = build_tree(read_resources(selected))
    except (OSError, ValueError):
        _tree = {}
    return _tree


def tree() -> dict[str, Any]:
    global _tree
    if _tree is None:
        reload()
    return _tree or {}


def read_bytes(name: str) -> bytes:
    value: Any = tree()
    for part in name.replace("\\", "/").split("/"):
        if not part:
            continue
        value = value[part]
    if not isinstance(value, bytes):
        raise IsADirectoryError(name)
    return value


def read_text(name: str, encoding: str = "utf-8", errors: str = "strict") -> str:
    return read_bytes(name).decode(encoding, errors)


class _EmbeddedModule(ModuleType, Mapping[str, Any]):
    def __getitem__(self, key: str) -> Any:
        return tree()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(tree())

    def __len__(self) -> int:
        return len(tree())

    def __contains__(self, key: object) -> bool:
        return key in tree()

    def keys(self):
        return tree().keys()

    def items(self):
        return tree().items()

    def values(self):
        return tree().values()

    def get(self, key: str, default: Any = None) -> Any:
        return tree().get(key, default)


sys.modules[__name__].__class__ = _EmbeddedModule


__all__ = ["read_bytes", "read_text", "reload", "tree"]
