"""project.py — asmpython project manifest (`project.json`) schema + IO.

A project file captures everything `asmpython build` needs beyond the entry
source file itself: output path/type, target platform(s), bundling mode,
icon, and native library dependencies (`packages`) plus where they should be
installed (`library_dirs`). `asmpython build some_project.json` reads this
instead of compiling the JSON file directly; `asmpython project new`
scaffolds a fresh one; `asmpython package install/uninstall some_project.json`
batch-installs/removes everything listed in `packages`.

Any project.json field can be overridden by the matching `build` CLI flag —
the CLI flag wins when given, the project file value is the default
otherwise (see `__main__.py`'s `cmd_build`).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_VALID_TARGETS = {"linux", "windows", "freestanding", "freestanding16"}
_VALID_OUTPUT_TYPES = {"executable", "library"}
_VALID_BUNDLE_MODES = {"onefile", "onedir"}

DEFAULT_PROJECT_FILENAME = "project.json"


class ProjectError(Exception):
    """Raised for a malformed or invalid project.json."""


@dataclass
class ProjectConfig:
    name: str = "project"
    entry: str = "main.py"
    output: str | None = None
    target: list[str] = field(default_factory=list)
    output_type: str = "executable"
    bundle_mode: str = "onefile"
    icon: str | None = None
    use_runtime_lib: bool = False
    library_dirs: list[str] = field(default_factory=lambda: ["libs"])
    packages: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for t in self.target:
            if t not in _VALID_TARGETS:
                raise ProjectError(
                    f"invalid target {t!r}; choose from {', '.join(sorted(_VALID_TARGETS))}"
                )
        if self.output_type not in _VALID_OUTPUT_TYPES:
            raise ProjectError(f"invalid type {self.output_type!r}")
        if self.bundle_mode not in _VALID_BUNDLE_MODES:
            raise ProjectError(f"invalid bundle_mode {self.bundle_mode!r}")
        if not self.library_dirs:
            raise ProjectError("library_dirs must have at least one entry")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> tuple["ProjectConfig", list[str]]:
        known: set = set()
        known.add("name")
        known.add("entry")
        known.add("output")
        known.add("target")
        known.add("output_type")
        known.add("bundle_mode")
        known.add("icon")
        known.add("use_runtime_lib")
        known.add("library_dirs")
        known.add("packages")
        unknown: list = sorted([k for k in data if k not in known])
        name: str = data["name"] if "name" in data else "project"
        entry: str = data["entry"] if "entry" in data else "main.py"
        output: str = data["output"] if "output" in data else None
        target: list = data["target"] if "target" in data else []
        output_type: str = data["output_type"] if "output_type" in data else "executable"
        bundle_mode: str = data["bundle_mode"] if "bundle_mode" in data else "onefile"
        icon: str = data["icon"] if "icon" in data else None
        use_runtime_lib: int = data["use_runtime_lib"] if "use_runtime_lib" in data else 0
        library_dirs: list = data["library_dirs"] if "library_dirs" in data else ["libs"]
        packages: list = data["packages"] if "packages" in data else []
        cfg: ProjectConfig = ProjectConfig(
            name=name,
            entry=entry,
            output=output,
            target=target,
            output_type=output_type,
            bundle_mode=bundle_mode,
            icon=icon,
            use_runtime_lib=use_runtime_lib,
            library_dirs=library_dirs,
            packages=packages,
        )
        cfg.validate()
        return cfg, unknown


def load_project(path: Path) -> ProjectConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ProjectError(f"{path}: invalid JSON ({e})") from e
    if not isinstance(raw, dict):
        raise ProjectError(f"{path}: project file must be a JSON object")
    cfg, unknown = ProjectConfig.from_dict(raw)
    if unknown:
        print(
            f"asmpython: warning: {path}: unknown project field(s) ignored: {', '.join(unknown)}",
            file=sys.stderr,
        )
    return cfg


def save_project(cfg: ProjectConfig, path: Path) -> None:
    path.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")


def find_default_project(start_dir: Path) -> Path | None:
    """Look for `project.json` directly inside *start_dir* (no upward
    search — keeps this predictable rather than magic)."""
    candidate = start_dir / DEFAULT_PROJECT_FILENAME
    return candidate if candidate.is_file() else None


def init_project(
    directory: Path, name: str, *, target: list[str] | None = None
) -> tuple[Path, ProjectConfig]:
    """Scaffold a new project under *directory*: project.json + entry.py +
    an empty library directory. Returns (project_json_path, config)."""
    directory.mkdir(parents=True, exist_ok=True)
    proj_path = directory / DEFAULT_PROJECT_FILENAME
    if proj_path.exists():
        raise ProjectError(f"{proj_path} already exists")

    cfg = ProjectConfig(name=name, entry="main.py", target=target or [])
    cfg.validate()
    save_project(cfg, proj_path)

    entry_path = directory / cfg.entry
    if not entry_path.exists():
        entry_path.write_text(f'print("Hello from {name}!")\n', encoding="utf-8")

    (directory / cfg.library_dirs[0]).mkdir(parents=True, exist_ok=True)
    return proj_path, cfg
