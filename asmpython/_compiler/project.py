"""asmpython project manifest (``project.json``) schema and IO.

A project file captures everything ``asmpython build`` needs beyond the entry
source file itself: output path/type, target platform(s), bundling mode, icon,
and native library dependencies (``packages``) plus their install directories.

Python/PyPI dependencies are intentionally absent from this schema.  They are
installed into the active Python environment with pip and discovered from that
interpreter's site-packages during native import resolution.
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
    # Explicit source roots for interpreter-only dynamic imports.  Static imports
    # are native-first and resolve from asmpython stdlib, then site-packages.
    pyinbin_imports: list[str] = field(default_factory=list)

    # Private compatibility attributes for the old CLI implementation.  They
    # are always empty and are deliberately omitted from project.json output.
    pypi_packages: list[str] = field(default_factory=list, init=False, repr=False)
    pypi_dir: str = field(default="", init=False, repr=False)

    def validate(self) -> None:
        for target in self.target:
            if target not in _VALID_TARGETS:
                raise ProjectError(
                    f"invalid target {target!r}; choose from "
                    + ", ".join(sorted(_VALID_TARGETS))
                )
        if self.output_type not in _VALID_OUTPUT_TYPES:
            raise ProjectError(f"invalid type {self.output_type!r}")
        if self.bundle_mode not in _VALID_BUNDLE_MODES:
            raise ProjectError(f"invalid bundle_mode {self.bundle_mode!r}")
        if not self.library_dirs:
            raise ProjectError("library_dirs must have at least one entry")
        if not isinstance(self.pyinbin_imports, list):
            raise ProjectError("pyinbin_imports must be a list of module roots")
        seen: list[str] = []
        for module in self.pyinbin_imports:
            if not isinstance(module, str) or not module:
                raise ProjectError(
                    "pyinbin_imports entries must be non-empty strings"
                )
            if module in seen:
                raise ProjectError(
                    f"pyinbin_imports contains duplicate module root {module!r}"
                )
            seen.append(module)

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("pypi_packages", None)
        data.pop("pypi_dir", None)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> tuple["ProjectConfig", list[str]]:
        known = {
            "name",
            "entry",
            "output",
            "target",
            "output_type",
            "bundle_mode",
            "icon",
            "use_runtime_lib",
            "library_dirs",
            "packages",
            "pyinbin_imports",
        }
        unknown = sorted(key for key in data if key not in known)
        cfg = cls(
            name=data.get("name", "project"),
            entry=data.get("entry", "main.py"),
            output=data.get("output"),
            target=data.get("target", []),
            output_type=data.get("output_type", "executable"),
            bundle_mode=data.get("bundle_mode", "onefile"),
            icon=data.get("icon"),
            use_runtime_lib=data.get("use_runtime_lib", False),
            library_dirs=data.get("library_dirs", ["libs"]),
            packages=data.get("packages", []),
            pyinbin_imports=data.get("pyinbin_imports", []),
        )
        cfg.validate()
        return cfg, unknown


def load_project(path: Path) -> ProjectConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProjectError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise ProjectError(f"{path}: project file must be a JSON object")
    cfg, unknown = ProjectConfig.from_dict(raw)
    if unknown:
        print(
            f"asmpython: warning: {path}: unknown project field(s) ignored: "
            + ", ".join(unknown),
            file=sys.stderr,
        )
    return cfg


def save_project(cfg: ProjectConfig, path: Path) -> None:
    path.write_text(json.dumps(cfg.to_dict(), indent=2) + "\n", encoding="utf-8")


def find_default_project(start_dir: Path) -> Path | None:
    """Look for project.json directly inside *start_dir* (no upward search)."""
    candidate = start_dir / DEFAULT_PROJECT_FILENAME
    return candidate if candidate.is_file() else None


def init_project(
    directory: Path,
    name: str,
    *,
    target: list[str] | None = None,
) -> tuple[Path, ProjectConfig]:
    """Scaffold project.json, an entry file, and the native library directory."""
    directory.mkdir(parents=True, exist_ok=True)
    project_path = directory / DEFAULT_PROJECT_FILENAME
    if project_path.exists():
        raise ProjectError(f"{project_path} already exists")

    cfg = ProjectConfig(name=name, entry="main.py", target=target or [])
    cfg.validate()
    save_project(cfg, project_path)

    entry_path = directory / cfg.entry
    if not entry_path.exists():
        entry_path.write_text(f'print("Hello from {name}!")\n', encoding="utf-8")

    (directory / cfg.library_dirs[0]).mkdir(parents=True, exist_ok=True)
    return project_path, cfg
