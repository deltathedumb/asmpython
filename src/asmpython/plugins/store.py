"""The list of installed plugins, on disk.

`asmpython plugin add mypack` has to outlive the process or it is not an
install, it is a flag. This is that file: a small JSON document naming the
modules to import on every run, and how each was resolved.

WHY RECORD THE SOURCES TOO. A plugin added with `--cwd 1` means "the one in
this directory", and re-resolving it later from PyPI because a package of the
same name exists would be a different plugin with the same name -- silently.
Storing the sources makes the second load ask the same question the first one
did.

The location follows the platform, and `ASMPYTHON_CONFIG_DIR` overrides it.
The override exists because tests must never touch a real user's file, and a
test that has to monkeypatch a module-level constant to be safe is one bad
merge away from not being safe.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .resolve import Sources

ENV_CONFIG_DIR = "ASMPYTHON_CONFIG_DIR"


@dataclass
class Entry:
    """One installed plugin."""

    name: str
    sources: Sources = field(default_factory=Sources)
    origin: str = ""
    source: str = ""
    #: What it registered when it was added, so `plugin list` can say without
    #: importing anything.
    provides: dict[str, list[str]] = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["sources"] = asdict(self.sources)
        return d

    @classmethod
    def from_json(cls, d: dict) -> Entry:
        raw = d.get("sources") or {}
        return cls(
            name=d["name"],
            sources=Sources(cwd=raw.get("cwd", True),
                            pypath=raw.get("pypath", True),
                            pip=raw.get("pip", False)),
            origin=d.get("origin", ""),
            source=d.get("source", ""),
            provides=d.get("provides", {}),
        )


def config_dir() -> Path:
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        return Path(base) / "asmpython"
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "asmpython"


def config_file() -> Path:
    return config_dir() / "plugins.json"


def read() -> list[Entry]:
    """Every installed plugin. An unreadable file is empty, not fatal.

    A corrupt config must not stop the compiler from compiling: the built-ins
    are enough to work, and a user who cannot run `asmpython plugin remove`
    because the plugin file is broken has no way out of it.
    """
    path = config_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, dict):
        return []
    out = []
    for item in raw.get("plugins", []):
        try:
            out.append(Entry.from_json(item))
        except (KeyError, TypeError):
            continue
    return out


def write(entries: list[Entry]) -> Path:
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "plugins": [e.to_json() for e in entries]}
    # Written whole then moved, so an interrupted write cannot leave a
    # half-file that read() would silently treat as "no plugins installed".
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def find(name: str) -> Entry | None:
    for entry in read():
        if entry.name == name:
            return entry
    return None


def add(entry: Entry) -> Path:
    """Insert or replace by name."""
    entries = [e for e in read() if e.name != entry.name]
    entries.append(entry)
    entries.sort(key=lambda e: e.name)
    return write(entries)


def remove(name: str) -> bool:
    entries = read()
    kept = [e for e in entries if e.name != name]
    if len(kept) == len(entries):
        return False
    write(kept)
    return True
