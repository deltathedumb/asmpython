"""packages.py — native runtime-library package management (`asmpython package ...`).

Packages here are prebuilt *binary* dependencies (DLLs / shared objects /
import libraries) that asmpython programs link or load at runtime but that
aren't themselves asmpython/Python source — SDL2 and friends are the
motivating case (see `asmpython/_vendor/sdl2/`, which `lumen` needs to link
against). `install`/`uninstall` resolve a package name against, in order:

  1. The bundled `_vendor/<name>/<platform>/` directory shipped with
     asmpython itself — instant, offline, no registry needed. This is how
     `sdl2` (and friends) installs by default today.
  2. A remote package registry: a JSON index (see `package-repository.json`
     at the repo root for the canonical format) mapping package name ->
     {versions: {version: {url, sha, verified}}}. Fetched over HTTP; falls
     back to a small bundled default registry if the network is
     unreachable.

A version entry's `sha` (sha256 of the downloaded archive) and `verified`
(whether asmpython's maintainers have vetted this exact artifact) fields
are independent signals, both optional:

  - `sha` present, hash matches: integrity confirmed. If `verified` is
    also true, install proceeds silently; if not, a warning notes the file
    matches its recorded checksum but hasn't been officially verified.
  - `sha` present, hash does NOT match: install is refused outright — this
    is a corrupted or tampered download, not just an unverified one.
  - `sha` absent: nothing to confirm integrity against; a warning fires
    unless `verified` is true (an unconditionally trusted entry).

Either way, the resolved binaries are copied flat into a destination
*library directory* (a project's `library_dirs[0]`, or `./libs/` with no
project in play — see `__main__.py`'s package subcommand for how that's
picked), and a small manifest (`.asmpython_packages.json` in that
directory) records what was installed so `uninstall` can remove exactly
those files again.

A registry entry may also offer one or more custom *project types* for
`asmpython project new --type <type>` (see `__main__.py`'s project
subcommand), under an `install.project-types` list::

    "somepkg": {
        ...,
        "install": {
            "project-types": [
                {
                    "type": "sdl2_game",
                    "init_script": {
                        "windows": {"script_type": "executable", "script": "tools/init.exe"},
                        "linux": {"script_type": "sh", "script": "tools/init.sh"}
                    }
                }
            ]
        }
    }

`script` is a path relative to the root of that package's *own* downloaded
archive (the same one named by its `versions[x].url`); `script_type` is
one of `"executable"`, `"batch"`/`"cmd"` (Windows only), or `"sh"` (Linux
only). `find_project_type`/`run_project_init_script` resolve and run it —
asmpython itself does nothing else on the new project's behalf (no
automatic binary install): the script is fully responsible for scaffolding
the project, including copying in any binaries it needs.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_REGISTRY_URL = (
    "https://raw.githubusercontent.com/deltathedumb/asmpython/main/package-repository.json"
)

MANIFEST_NAME = ".asmpython_packages.json"

# Minimal built-in fallback so `install` degrades gracefully (rather than
# failing outright) when both the network and $ASMPYTHON_PACKAGE_REGISTRY
# are unavailable. Mirrors package-repository.json's current content; the
# live registry is always preferred when it's reachable.
_BUNDLED_REGISTRY: dict = {
    "sdl2": {
        "name": "SDL2",
        "author": "Sam Lantinga",
        "license": "zlib",
        "latest": "2.32.10",
        "latest-beta": None,
        "latest-alpha": None,
        "versions": {
            "2.32.10": {
                "url": "https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-devel-2.32.10-mingw.tar.gz",
                "sha": "83a5d74012311edc3c0d40ea6faecbe57ad692aa033fa5dc273cc937e3938ff2",
                "verified": True,
            },
        },
    },
}

BINARY_EXTS = [".dll", ".so", ".dylib", ".a", ".lib"]


class PackageError(Exception):
    """Raised for any package install/uninstall failure."""


# ---------------------------------------------------------------------------
# Vendored (bundled, offline) lookup
# ---------------------------------------------------------------------------

def _vendor_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "_vendor"


def _host_platform_dir() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def vendored_files(name: str) -> list[Path]:
    """Binary files asmpython bundles for *name* on the host platform, or
    an empty list if this package isn't vendored at all."""
    d = _vendor_dir() / name.lower() / _host_platform_dir()
    if not d.is_dir():
        return []
    return [p for p in d.iterdir() if p.is_file()]


# ---------------------------------------------------------------------------
# Registry (remote/local JSON index)
# ---------------------------------------------------------------------------

def load_registry(registry_url: str | None = None) -> dict:
    """Fetch the package registry JSON.

    Resolution order: explicit *registry_url* argument, then
    $ASMPYTHON_PACKAGE_REGISTRY, then `DEFAULT_REGISTRY_URL`. A value that
    names an existing local file is read directly instead of fetched over
    HTTP (handy for `--registry ./package-repository.json` during
    development). Falls back to a small bundled default registry if every
    remote attempt fails.
    """
    url = registry_url or os.environ.get("ASMPYTHON_PACKAGE_REGISTRY") or DEFAULT_REGISTRY_URL
    local_path = Path(url)
    try:
        if local_path.is_file():
            return json.loads(local_path.read_text(encoding="utf-8"))
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(
            f"asmpython: warning: could not fetch package registry ({e}); "
            "falling back to the bundled offline registry",
            file=sys.stderr,
        )
        return _BUNDLED_REGISTRY


def _resolve_version(name: str, entry: dict, version: str | None) -> tuple[str, dict]:
    """Look up *version* (or the registry's 'latest') in *entry*. Returns
    ``(resolved_version, version_info)`` — the caller checks `url`/`sha`/
    `verified` on `version_info` itself once the file is in hand, since the
    sha check needs the downloaded bytes."""
    versions: dict = entry.get("versions") or {}
    if not versions:
        raise PackageError(f"{name!r}: registry entry has no versions")
    if version is None:
        version = entry.get("latest")
        if version is None:
            raise PackageError(
                f"{name!r}: registry entry has no 'latest' version; pass --version explicitly"
            )
    info = versions.get(version)
    if info is None:
        raise PackageError(
            f"{name!r}: version {version!r} not found; available: "
            f"{', '.join(sorted(versions)) or '(none)'}"
        )
    if not info.get("url"):
        raise PackageError(f"{name!r}: version {version!r} has no download url")
    return version, info


def _sha256_of(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_integrity(name: str, version: str, info: dict, archive: Path) -> None:
    """Verify *archive* against `info`'s `sha`/`verified` fields, raising on
    a confirmed hash mismatch (corrupted/tampered download) and warning on
    everything short of "verified and hash-confirmed"."""
    expected_sha = info.get("sha")
    verified = bool(info.get("verified", False))

    if expected_sha:
        actual_sha = _sha256_of(archive)
        if actual_sha.lower() != str(expected_sha).lower():
            raise PackageError(
                f"{name!r} {version}: checksum mismatch — expected {expected_sha}, "
                f"got {actual_sha}. The download may be corrupted or tampered with; "
                "refusing to install."
            )
        if not verified:
            print(
                f"asmpython: warning: {name!r} {version} matches its recorded checksum "
                "but has not been officially verified by us",
                file=sys.stderr,
            )
        return

    if not verified:
        print(
            f"asmpython: warning: {name!r} {version} has no checksum on record and has "
            "not been officially verified by us — installing on trust alone",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Download + extract
# ---------------------------------------------------------------------------

def _download(url: str, dest: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except (urllib.error.URLError, OSError) as e:
        raise PackageError(f"failed to download {url}: {e}") from e


def _extract(archive: Path, dest_dir: Path) -> None:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest_dir)
        return
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(dest_dir)
        return
    except tarfile.ReadError:
        pass
    # Not a recognized archive -- treat the download itself as the one binary.
    shutil.copy2(archive, dest_dir / archive.name)


def _select_binary_files(root: Path) -> list[Path]:
    """Pick the binary files relevant to the host build out of everything an
    extracted archive contains. mingw-w64 "devel" archives ship 32- and
    64-bit trees side by side (e.g. `x86_64-w64-mingw32/` and
    `i686-w64-mingw32/`); prefer 64-bit plus anything with no arch split at
    all, falling back to everything found if nothing looked 64-bit.
    """
    all_files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in BINARY_EXTS]
    if not all_files:
        return []

    def _is_64(p: Path) -> bool:
        parts = {part.lower() for part in p.parts}
        return any("x86_64" in part or part in ("64", "x64") for part in parts)

    def _is_32(p: Path) -> bool:
        parts = {part.lower() for part in p.parts}
        return any(part in ("i686", "x86", "32") or "i386" in part for part in parts)

    sixty_four = [p for p in all_files if _is_64(p)]
    if sixty_four:
        unhinted = [p for p in all_files if not _is_64(p) and not _is_32(p)]
        return sixty_four + unhinted
    return all_files


# ---------------------------------------------------------------------------
# Manifest (records what install put where, for uninstall)
# ---------------------------------------------------------------------------

def _read_manifest(dest_dir: Path) -> dict:
    p = dest_dir / MANIFEST_NAME
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_manifest(dest_dir: Path, manifest: dict) -> None:
    (dest_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install_package(
    name: str,
    dest_dir: Path,
    *,
    version: str | None = None,
    registry_url: str | None = None,
) -> tuple[str, list[str]]:
    """Install *name* (optionally pinned to *version*) into *dest_dir*.

    Returns ``(resolved_version, [installed filenames])``.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    key = name.lower()

    vendored = vendored_files(key) if version is None else []
    if vendored:
        installed = [f.name for f in vendored]
        for f in vendored:
            shutil.copy2(f, dest_dir / f.name)
        resolved_version = "vendored"
    else:
        registry = load_registry(registry_url)
        entry = registry.get(key)
        if entry is None:
            raise PackageError(f"unknown package {name!r} (not vendored, not in the registry)")
        resolved_version, info = _resolve_version(name, entry, version)
        url = info["url"]
        with tempfile.TemporaryDirectory(prefix="asmpython-pkg-") as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / (Path(url.split("?")[0]).name or "package.bin")
            print(f"asmpython: downloading {name} {resolved_version} from {url}", file=sys.stderr)
            _download(url, archive)
            _check_integrity(name, resolved_version, info, archive)
            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()
            _extract(archive, extract_dir)
            picked = _select_binary_files(extract_dir)
            if not picked:
                raise PackageError(
                    f"no recognized binary files ({', '.join(BINARY_EXTS)}) found in {url}"
                )
            installed = [f.name for f in picked]
            for f in picked:
                shutil.copy2(f, dest_dir / f.name)

    manifest = _read_manifest(dest_dir)
    manifest[key] = {"version": resolved_version, "files": sorted(set(installed))}
    _write_manifest(dest_dir, manifest)
    return resolved_version, installed


def uninstall_package(name: str, dest_dir: Path) -> list[str]:
    """Remove a previously-installed package's files from *dest_dir*.

    Returns the list of filenames actually removed.
    """
    key = name.lower()
    manifest = _read_manifest(dest_dir)
    entry = manifest.pop(key, None)
    if entry is None:
        raise PackageError(f"package {name!r} is not installed in {dest_dir}")
    removed = []
    for fname in entry.get("files", []):
        fpath = dest_dir / fname
        if fpath.is_file():
            fpath.unlink()
            removed.append(fname)
    _write_manifest(dest_dir, manifest)
    return removed


def list_installed(dest_dir: Path) -> dict:
    """Return the manifest dict (`{name: {version, files}}`) for *dest_dir*."""
    return _read_manifest(dest_dir)


# ---------------------------------------------------------------------------
# Custom project types (`asmpython project new --type <type>`)
# ---------------------------------------------------------------------------

def find_project_type(
    type_name: str,
    *,
    from_pkg: str | None = None,
    registry_url: str | None = None,
) -> tuple[str, dict]:
    """Search the registry for a package whose `install.project-types`
    defines *type_name*. Returns ``(package_name, project_type_entry)``.

    If *from_pkg* is given, only that package is checked. Raises
    PackageError if no package defines *type_name*, or if more than one
    does and *from_pkg* wasn't given to disambiguate.
    """
    registry = load_registry(registry_url)
    keys = [from_pkg.lower()] if from_pkg else list(registry.keys())
    matches: list[tuple[str, dict]] = []
    for key in keys:
        entry = registry.get(key)
        if entry is None:
            continue
        install: dict = entry.get("install") or {}
        project_types: list = install.get("project-types") or []
        for pt in project_types:
            pt_type: str = pt.get("type") or ""
            if pt_type == type_name:
                matches.append((key, pt))
    if not matches:
        if from_pkg:
            raise PackageError(
                f"package {from_pkg!r} does not define project type {type_name!r}"
            )
        raise PackageError(f"no package in the registry defines project type {type_name!r}")
    if len(matches) > 1:
        owners = ", ".join(sorted(m[0] for m in matches))
        raise PackageError(
            f"project type {type_name!r} is defined by more than one package "
            f"({owners}); pass --from to pick one"
        )
    return matches[0]


def _host_script_platform() -> str:
    """Key into an `init_script` dict matching the host OS. There's no
    'macos' key in the schema (script authors are expected to ship a 'sh'
    script that covers both Unixes), so macOS falls back to 'linux'."""
    if sys.platform == "win32":
        return "windows"
    return "linux"


def _run_init_script(script_type: str, script_path: Path, dest_dir: Path) -> None:
    """Invoke a resolved init_script file against *dest_dir*."""
    if script_type == "executable":
        cmd = f'"{script_path}" "{dest_dir}"'
    elif script_type in ("batch", "cmd"):
        cmd = f'cmd /c "\"{script_path}\" \"{dest_dir}\""'
    elif script_type == "sh":
        cmd = f'sh "{script_path}" "{dest_dir}"'
    else:
        raise PackageError(f"unknown init_script script_type {script_type!r}")
    print("asmpython: running project init script:", cmd, file=sys.stderr)
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise PackageError(f"project init script exited with status {proc.returncode}")


def run_project_init_script(
    pkg_name: str,
    project_type_entry: dict,
    dest_dir: Path,
    *,
    version: str | None = None,
    registry_url: str | None = None,
) -> None:
    """Download+extract *pkg_name*'s archive, then run the platform-matching
    `init_script` named in *project_type_entry* (as returned by
    `find_project_type`) against *dest_dir*.

    The script is solely responsible for scaffolding the new project; no
    binaries are installed automatically here.
    """
    init_script: dict = project_type_entry.get("init_script") or {}
    plat: str = _host_script_platform()
    plat_info: dict = init_script.get(plat) or {}
    if not plat_info:
        raise PackageError(
            f"project type {project_type_entry.get('type')!r} has no init_script "
            f"entry for platform {plat!r}"
        )
    script_type: str = plat_info.get("script_type") or ""
    script_rel: str = plat_info.get("script") or ""
    if not script_rel:
        raise PackageError(
            f"project type {project_type_entry.get('type')!r}: init_script.{plat} "
            "has no 'script' path"
        )

    registry = load_registry(registry_url)
    entry = registry.get(pkg_name.lower())
    if entry is None:
        raise PackageError(f"unknown package {pkg_name!r} (not in the registry)")
    resolved_version, info = _resolve_version(pkg_name, entry, version)
    url = info["url"]
    with tempfile.TemporaryDirectory(prefix="asmpython-init-") as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / (Path(url.split("?")[0]).name or "package.bin")
        print(f"asmpython: downloading {pkg_name} {resolved_version} from {url}", file=sys.stderr)
        _download(url, archive)
        _check_integrity(pkg_name, resolved_version, info, archive)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        _extract(archive, extract_dir)

        script_path = extract_dir / script_rel
        if not script_path.is_file():
            raise PackageError(
                f"project type {project_type_entry.get('type')!r}: init_script "
                f"{script_rel!r} not found in {pkg_name} {resolved_version}'s archive"
            )
        _run_init_script(script_type, script_path, dest_dir)
