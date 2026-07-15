"""Build deterministic source bundles consumed by the future pyinbin VM.

The compiler's normal whole-program loader statically merges asmpython-native
modules.  A pyinbin bundle is intentionally different: it carries Python
source and module metadata for a runtime interpreter, so an executable can
load a declared module root without CPython being present on the target.

This module only creates/verifies the package format.  The native loader and
VM are separate delivery steps; callers must not enable runtime import routing
until those pieces are available.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


MANIFEST_NAME = "manifest.json"
FORMAT_VERSION = 1


class PyinbinPackageError(Exception):
    """Raised when a declared runtime-import root cannot be packaged."""


@dataclass(frozen=True)
class PackedModule:
    name: str
    path: str
    sha256: str
    size: int


def _validate_module_root(module: str) -> list[str]:
    parts = module.split(".")
    if not module or any(not part.isidentifier() for part in parts):
        raise PyinbinPackageError(f"invalid pyinbin module root {module!r}")
    return parts


def _root_path(project_root: Path, module: str) -> Path:
    parts = _validate_module_root(module)
    base = project_root.joinpath(*parts)
    module_file = base.with_suffix(".py")
    package_init = base / "__init__.py"
    if module_file.is_file():
        return module_file
    if package_init.is_file():
        return base
    raise PyinbinPackageError(
        f"pyinbin module root {module!r} was not found under {project_root}"
    )


def _module_name(project_root: Path, source: Path) -> str:
    rel = source.relative_to(project_root)
    if rel.name == "__init__.py":
        parts = rel.parent.parts
    else:
        parts = (*rel.parent.parts, rel.stem)
    if not parts or any(not part.isidentifier() for part in parts):
        raise PyinbinPackageError(f"source path cannot be imported as a module: {rel}")
    return ".".join(parts)


def _sources_for_root(project_root: Path, module: str) -> list[Path]:
    root = _root_path(project_root, module)
    if root.is_file():
        return [root]
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def build_source_bundle(
    project_root: Path,
    module_roots: list[str],
    destination: Path,
) -> list[PackedModule]:
    """Write a deterministic pyinbin source bundle.

    ``destination`` must be empty or already contain a pyinbin manifest. This
    guard prevents a build from deleting an unrelated user directory. Sources
    are copied under ``src/`` preserving their project-relative paths; the
    manifest maps each qualified module name to that path and its digest.
    """
    project_root = project_root.resolve()
    destination = destination.resolve()
    manifest_path = destination / MANIFEST_NAME
    if destination.exists() and any(destination.iterdir()) and not manifest_path.is_file():
        raise PyinbinPackageError(
            f"refusing to replace non-pyinbin directory {destination}"
        )

    source_paths: dict[str, Path] = {}
    for root in sorted(module_roots):
        for source in _sources_for_root(project_root, root):
            name = _module_name(project_root, source)
            previous = source_paths.get(name)
            if previous is not None and previous != source:
                raise PyinbinPackageError(f"duplicate module {name!r} in pyinbin roots")
            source_paths[name] = source

    destination.mkdir(parents=True, exist_ok=True)
    packed: list[PackedModule] = []
    for name, source in sorted(source_paths.items()):
        rel = source.relative_to(project_root)
        data = source.read_bytes()
        target = destination / "src" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        packed.append(
            PackedModule(
                name=name,
                path=(Path("src") / rel).as_posix(),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
        )

    manifest = {
        "format": "asmpython.pyinbin",
        "version": FORMAT_VERSION,
        "roots": sorted(module_roots),
        "modules": [module.__dict__ for module in packed],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return packed


def verify_source_bundle(destination: Path) -> list[PackedModule]:
    """Validate bundle metadata and return its modules in manifest order."""
    destination = destination.resolve()
    try:
        manifest = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PyinbinPackageError(f"invalid pyinbin manifest in {destination}") from exc
    if manifest.get("format") != "asmpython.pyinbin" or manifest.get("version") != FORMAT_VERSION:
        raise PyinbinPackageError("unsupported pyinbin bundle format")

    packed: list[PackedModule] = []
    seen: set[str] = set()
    source_root = (destination / "src").resolve()
    for item in manifest.get("modules", []):
        try:
            module = PackedModule(
                name=item["name"], path=item["path"], sha256=item["sha256"], size=item["size"]
            )
        except (KeyError, TypeError) as exc:
            raise PyinbinPackageError("invalid module entry in pyinbin manifest") from exc
        source_path = (destination / module.path).resolve()
        if (
            module.name in seen
            or not module.path.startswith("src/")
            or not source_path.is_relative_to(source_root)
        ):
            raise PyinbinPackageError("invalid or duplicate pyinbin module entry")
        data = source_path.read_bytes()
        if len(data) != module.size or hashlib.sha256(data).hexdigest() != module.sha256:
            raise PyinbinPackageError(f"pyinbin source integrity check failed for {module.name}")
        seen.add(module.name)
        packed.append(module)
    return packed
