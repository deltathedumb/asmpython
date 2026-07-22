"""Install, package, discover, verify, and load ``.apext`` extensions."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from asmpython.extension import Extension


FORMAT = "asmpython.apext"
FORMAT_VERSION = 1
MANIFEST = "apext.json"
SCOPES = ("system", "user", "local")
_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".asmpython", ".venv", "venv", "env",
    "__pycache__", "build", "dist", ".mypy_cache", ".pytest_cache",
}


class ExtensionPackageError(Exception):
    pass


@dataclass(frozen=True)
class InstalledExtension:
    id: str
    version: str
    scope: str
    path: Path
    production_suitable: bool


def scope_path(scope: str, directory: Path | None = None) -> Path:
    if scope not in SCOPES:
        raise ExtensionPackageError(f"unknown extension scope {scope!r}")
    if scope == "local":
        return (directory or Path.cwd()).resolve() / ".asmpython" / "extensions"
    if scope == "user":
        if os.name == "nt":
            base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            return base / "ASMPython" / "extensions"
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return base / "asmpython" / "extensions"
    if os.name == "nt":
        base = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        return base / "ASMPython" / "extensions"
    return Path("/etc/asmpython/extensions")


def _safe_archive_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ExtensionPackageError(f"unsafe archive member {name!r}")
    return path.as_posix()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_manifest(path: Path, *, verify: bool = True) -> dict[str, Any]:
    if not path.is_file():
        raise ExtensionPackageError(f"extension package not found: {path}")
    if not zipfile.is_zipfile(path):
        raise ExtensionPackageError(f"{path} is not a valid .apext ZIP archive")
    with zipfile.ZipFile(path) as archive:
        try:
            manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
        except KeyError as exc:
            raise ExtensionPackageError(f"{path} is missing {MANIFEST}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExtensionPackageError(f"{path} has a malformed {MANIFEST}: {exc}") from exc
        if manifest.get("format") != FORMAT or manifest.get("format_version") != FORMAT_VERSION:
            raise ExtensionPackageError(
                f"{path} uses unsupported extension format/version: "
                f"{manifest.get('format')!r}/{manifest.get('format_version')!r}"
            )
        extension_id = manifest.get("id")
        entry = manifest.get("entry")
        object_name = manifest.get("object")
        if not isinstance(extension_id, str) or not extension_id:
            raise ExtensionPackageError(f"{path}: manifest field 'id' is required")
        if not isinstance(entry, str) or not entry.endswith(".py"):
            raise ExtensionPackageError(f"{path}: manifest field 'entry' must name a Python file")
        if not isinstance(object_name, str) or not object_name.isidentifier():
            raise ExtensionPackageError(f"{path}: manifest field 'object' must be an identifier")
        entry = _safe_archive_name(entry)
        names = {_safe_archive_name(name) for name in archive.namelist() if not name.endswith("/")}
        if entry not in names:
            raise ExtensionPackageError(f"{path}: entry {entry!r} is missing from the archive")
        files = manifest.get("files", {})
        if verify:
            if not isinstance(files, dict):
                raise ExtensionPackageError(f"{path}: manifest 'files' must be an object")
            for name, expected in files.items():
                safe_name = _safe_archive_name(str(name))
                if safe_name == MANIFEST:
                    continue
                if safe_name not in names:
                    raise ExtensionPackageError(f"{path}: hashed file {safe_name!r} is missing")
                actual = _sha256(archive.read(safe_name))
                if actual != expected:
                    raise ExtensionPackageError(
                        f"{path}: SHA-256 mismatch for {safe_name!r}: {actual} != {expected}"
                    )
        return manifest


def _load_descriptor(module_ref: str, root: Path) -> tuple[Extension, Path, str]:
    if ":" not in module_ref:
        raise ExtensionPackageError("package target must use module:object syntax")
    module_name, object_name = module_ref.rsplit(":", 1)
    if not module_name or not object_name.isidentifier():
        raise ExtensionPackageError("package target must use module:object syntax")
    module_path = root.joinpath(*module_name.split("."))
    if module_path.with_suffix(".py").is_file():
        source_path = module_path.with_suffix(".py")
    elif (module_path / "__init__.py").is_file():
        source_path = module_path / "__init__.py"
    else:
        raise ExtensionPackageError(f"cannot resolve module {module_name!r} beneath {root}")
    unique_name = f"_asmpython_apext_package_{hashlib.sha256(str(source_path).encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(unique_name, source_path)
    if spec is None or spec.loader is None:
        raise ExtensionPackageError(f"cannot import extension entry {source_path}")
    module = importlib.util.module_from_spec(spec)
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(root))
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ExtensionPackageError(f"failed to import {module_ref}: {exc}") from exc
    finally:
        sys.path[:] = old_path
    descriptor = getattr(module, object_name, None)
    if not isinstance(descriptor, Extension):
        raise ExtensionPackageError(
            f"{module_ref} did not resolve to an asmpython.Extension object"
        )
    return descriptor, source_path, object_name


def _project_files(root: Path, output: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.resolve() == output.resolve():
            continue
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix == ".apext":
            continue
        files.append(path)
    return files


def package_extension(
    module_ref: str,
    *,
    root: Path | None = None,
    output: Path | None = None,
) -> Path:
    root = (root or Path.cwd()).resolve()
    descriptor, source_path, object_name = _load_descriptor(module_ref, root)
    output = (output or root / f"{descriptor.id}.apext").resolve()
    entry = source_path.relative_to(root).as_posix()
    files = _project_files(root, output)
    if source_path not in files:
        files.append(source_path)
        files.sort()
    payloads: dict[str, bytes] = {
        path.relative_to(root).as_posix(): path.read_bytes() for path in files
    }
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": descriptor.id,
        "version": descriptor.version,
        "description": descriptor.description,
        "api_version": descriptor.api_version,
        "production_suitable": descriptor.production_suitable,
        "entry": entry,
        "object": object_name,
        "module": module_ref.rsplit(":", 1)[0],
        "files": {name: _sha256(data) for name, data in sorted(payloads.items())},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    timestamp = (1980, 1, 1, 0, 0, 0)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest_info = zipfile.ZipInfo(MANIFEST, timestamp)
            manifest_info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(
                manifest_info,
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            )
            for name, data in sorted(payloads.items()):
                info = zipfile.ZipInfo(_safe_archive_name(name), timestamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()
    return output


def install_extension(
    package: Path,
    *,
    scope: str = "user",
    directory: Path | None = None,
) -> InstalledExtension:
    manifest = read_manifest(package, verify=True)
    destination_root = scope_path(scope, directory)
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / f"{manifest['id']}.apext"
    temporary = destination.with_suffix(".apext.tmp")
    shutil.copyfile(package, temporary)
    read_manifest(temporary, verify=True)
    temporary.replace(destination)
    return InstalledExtension(
        id=manifest["id"],
        version=str(manifest.get("version", "0.0.0")),
        scope=scope,
        path=destination,
        production_suitable=bool(manifest.get("production_suitable", True)),
    )


def uninstall_extension(
    extension_id: str,
    *,
    scope: str | None = None,
    directory: Path | None = None,
) -> list[Path]:
    scopes = (scope,) if scope else ("local", "user", "system")
    removed: list[Path] = []
    for item_scope in scopes:
        path = scope_path(item_scope, directory) / f"{extension_id}.apext"
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed


def list_installed(directory: Path | None = None) -> list[InstalledExtension]:
    installed: list[InstalledExtension] = []
    for scope in SCOPES:
        root = scope_path(scope, directory)
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.apext")):
            try:
                manifest = read_manifest(path, verify=False)
            except ExtensionPackageError:
                continue
            installed.append(InstalledExtension(
                id=manifest["id"],
                version=str(manifest.get("version", "0.0.0")),
                scope=scope,
                path=path,
                production_suitable=bool(manifest.get("production_suitable", True)),
            ))
    return installed


def get_extension(
    url: str,
    *,
    scope: str = "user",
    directory: Path | None = None,
    expected_sha256: str | None = None,
    allow_http: bool = False,
) -> InstalledExtension:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ({"https"} if not allow_http else {"https", "http"}):
        raise ExtensionPackageError("extension URLs must use HTTPS (or pass --allow-http)")
    with tempfile.TemporaryDirectory(prefix="asmpython_apext_get_") as temp:
        target = Path(temp) / "download.apext"
        try:
            with urllib.request.urlopen(url) as response, target.open("wb") as output:
                shutil.copyfileobj(response, output)
        except Exception as exc:
            raise ExtensionPackageError(f"failed to download {url}: {exc}") from exc
        digest = _sha256(target.read_bytes())
        if expected_sha256 and digest.lower() != expected_sha256.lower():
            raise ExtensionPackageError(
                f"download SHA-256 mismatch: {digest} != {expected_sha256.lower()}"
            )
        return install_extension(target, scope=scope, directory=directory)


def _load_archive(path: Path) -> Extension:
    manifest = read_manifest(path, verify=True)
    entry = manifest["entry"]
    object_name = manifest["object"]
    with zipfile.ZipFile(path) as archive:
        source = archive.read(entry).decode("utf-8")
    namespace: dict[str, Any] = {
        "__name__": f"asmpython_apext_{manifest['id'].replace('-', '_').replace('.', '_')}",
        "__file__": f"{path}!/{entry}",
        "__package__": "",
    }
    old_path = list(sys.path)
    try:
        sys.path.insert(0, str(path))
        exec(compile(source, namespace["__file__"], "exec"), namespace)
    except Exception as exc:
        raise ExtensionPackageError(f"failed to load extension {manifest['id']!r}: {exc}") from exc
    finally:
        sys.path[:] = old_path
    descriptor = namespace.get(object_name)
    if not isinstance(descriptor, Extension):
        raise ExtensionPackageError(
            f"{path}: object {object_name!r} is not an asmpython.Extension"
        )
    if descriptor.id != manifest["id"]:
        raise ExtensionPackageError(
            f"{path}: descriptor id {descriptor.id!r} does not match manifest id {manifest['id']!r}"
        )
    if descriptor.api_version != manifest.get("api_version", descriptor.api_version):
        raise ExtensionPackageError(f"{path}: descriptor and manifest API versions disagree")
    descriptor.activate()
    return descriptor


def load_installed_extensions(directory: Path | None = None) -> list[InstalledExtension]:
    """Load installed extensions with local > user > system precedence."""

    chosen: dict[str, InstalledExtension] = {}
    for item in list_installed(directory):
        chosen[item.id] = item
    loaded: list[InstalledExtension] = []
    for extension_id in sorted(chosen):
        item = chosen[extension_id]
        _load_archive(item.path)
        loaded.append(item)
    return loaded


__all__ = [
    "ExtensionPackageError", "InstalledExtension", "SCOPES", "get_extension",
    "install_extension", "list_installed", "load_installed_extensions",
    "package_extension", "read_manifest", "scope_path", "uninstall_extension",
]
