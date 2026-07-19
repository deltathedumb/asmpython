"""pypi.py — real PyPI Python-package installation (`asmpython pypi ...`).

Distinct from `packages.py` (`asmpython package ...`), which installs
prebuilt *binary* dependencies (DLLs/.so, e.g. SDL2) — this module installs
real Python *source*, resolved against the actual public PyPI JSON API
(https://pypi.org/pypi/<name>/json), for programs to `import` and run
through pyinbin (asmpython's fallback interpreter). The two systems are
kept deliberately separate: they install fundamentally different kinds of
artifact (a flat pile of binaries vs. an importable Python package tree)
under a different trust model, and conflating them risked real confusion.

v1 scope, deliberately narrow:

  - **pyinbin-only.** Real PyPI source almost always uses constructs
    asmpython's native compiler subset doesn't support (decorators,
    `*args`/`**kwargs`, comprehensions, f-string `=`, exception classes,
    closures beyond module scope, ...). Rather than pretend native
    compilation is a live option per-package, every installed PyPI package
    is treated as an implicit pyinbin import root — see `__main__.py`'s
    wiring into `pyinbin_fallback()`.
  - **Wheels only, no sdist.** A wheel (`.whl`) is a zip with a known
    internal layout that can be inspected and extracted without executing
    any code. A source distribution (`.tar.gz`) needs a build step
    (`setup.py`/a PEP 517 backend) to produce installable files — that's
    arbitrary code execution during install, which breaks the "installing
    a package is safe to do without reading it first" property this
    module (and `packages.py` before it) both maintain. A package that
    ships no wheel is refused with a clear error naming what WAS
    available.
  - **Pure-Python wheels only.** A wheel containing a compiled `.pyd`/
    `.so`/`.dylib` extension module is refused outright, naming the
    specific member(s) found — neither the native backend nor pyinbin has
    any way to load a CPython-C-API-compiled extension, and there is no
    plan to build one; that's a fundamentally different, much larger
    project.
  - **No transitive dependency resolution.** The caller (a project's
    `pypi_packages` list, or repeated `asmpython pypi install` calls)
    must name every package it needs explicitly. A missing transitive
    dependency surfaces as an ordinary `ImportError` at pyinbin runtime —
    an honest, if unhelpful-until-you-add-the-missing-package, v1
    limitation. A real resolver (backtracking version solving across a
    dependency graph, the way pip's does) is a substantial standalone
    project of its own, not attempted here.
  - **No private/authenticated indexes.** Only the public PyPI JSON API,
    one hardcoded base URL, no `--extra-index-url` equivalent. Mirrors
    `packages.py`'s own "one registry URL at a time" posture.

Security: wheels are sha256-verified against the digest PyPI's own JSON
API always supplies for every file (`urls[].digests.sha256` — unlike
`packages.py`'s binary registry, this is never optional, so a mismatch is
always a hard failure, never a soft warning). No code from a downloaded
wheel is ever executed during install — only `zipfile` reads (metadata
parsing, extraction) and `hashlib` (integrity checking).
"""
from __future__ import annotations

import email.parser
import hashlib
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PYPI_JSON_BASE = "https://pypi.org/pypi"

MANIFEST_NAME = ".asmpython_pypi_packages.json"

# Compiled-extension suffixes a pure-Python wheel must not contain.
_NATIVE_EXT_SUFFIXES = (".pyd", ".so", ".dylib")

# Wheel filename: {name}-{version}(-{build})?-{python tag}-{abi tag}-{platform tag}.whl
_WHEEL_NAME_RE = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)"
    r"(?:-(?P<build>\d[^-]*))?"
    r"-(?P<pytag>[^-]+)-(?P<abitag>[^-]+)-(?P<platform>[^-]+)\.whl$"
)


class PypiError(Exception):
    pass


@dataclass
class WheelInfo:
    name: str
    version: str
    requires_dist: list = field(default_factory=list)
    has_native_extension: bool = False
    native_extension_members: list = field(default_factory=list)
    record_members: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# PyPI JSON API resolution
# ---------------------------------------------------------------------------

def resolve_pypi_package(name: str, version: "str | None" = None) -> dict:
    """Fetch PyPI's JSON API metadata for *name* (optionally pinned to
    *version*). Returns the raw decoded JSON response (a real dict with
    `info`/`urls`/`releases` keys, see
    https://warehouse.pypa.io/api-reference/json.html)."""
    if version:
        url = f"{PYPI_JSON_BASE}/{name}/{version}/json"
    else:
        url = f"{PYPI_JSON_BASE}/{name}/json"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            what = f"{name}=={version}" if version else name
            raise PypiError(f"no PyPI package found for {what!r}") from e
        raise PypiError(f"failed to query PyPI for {name!r}: {e}") from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise PypiError(f"failed to query PyPI for {name!r}: {e}") from e
    return data


def _is_pure_python_wheel_tag(pytag: str, abitag: str, platform: str) -> bool:
    """True for a genuinely platform-independent wheel: abi tag `none` and
    platform tag `any`. A precise check, not a substring/heuristic one --
    a wheel can have NO compiled extension inside it and still be tagged
    for a specific CPython ABI/platform (e.g. a pure-Python package that
    happens to be built per-platform for other reasons); such a wheel is
    not portable in the sense this module needs and is rejected here
    rather than accepted on a false "no .pyd/.so found" technicality."""
    return abitag == "none" and platform == "any"


def _select_wheel(urls: list) -> dict:
    """Pick the best wheel entry from a PyPI release's `urls` list: prefer
    a universal (`py3-none-any` or `py2.py3-none-any`) pure-Python wheel.
    Raises PypiError, listing what WAS available, if no such wheel exists
    (only sdist and/or platform-specific wheels)."""
    wheel_entries = [u for u in urls if u.get("packagetype") == "bdist_wheel"]
    candidates = []
    for u in wheel_entries:
        m = _WHEEL_NAME_RE.match(u.get("filename", ""))
        if not m:
            continue
        for pytag in m.group("pytag").split("."):
            if _is_pure_python_wheel_tag(pytag, m.group("abitag"), m.group("platform")):
                candidates.append(u)
                break
    if candidates:
        # Prefer py3-none-any over py2.py3-none-any over any other match,
        # for the newest/most specific tag first.
        def _rank(u: dict) -> int:
            fn = u.get("filename", "")
            if "-py3-none-any.whl" in fn:
                return 0
            if "-py2.py3-none-any.whl" in fn:
                return 1
            return 2
        candidates.sort(key=_rank)
        return candidates[0]

    available = [u.get("filename", "?") for u in urls] or ["(nothing)"]
    raise PypiError(
        "no pure-Python wheel available (v1 only installs wheels tagged "
        "abi=none, platform=any) -- found: " + ", ".join(available) + ". "
        "This package cannot be installed by `asmpython pypi install` yet."
    )


# ---------------------------------------------------------------------------
# Download + integrity
# ---------------------------------------------------------------------------

def _sha256_of_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_bytes(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            return resp.read()
    except (urllib.error.URLError, OSError) as e:
        raise PypiError(f"failed to download {url}: {e}") from e


def _check_integrity(name: str, version: str, expected_sha256: "str | None", data: bytes) -> None:
    """PyPI's JSON API always supplies a sha256 digest for every file --
    unlike packages.py's binary registry, there is no optional/unverified
    middle ground here: a present digest that doesn't match is always a
    hard failure, and an ABSENT digest (which PyPI's API is not expected
    to ever omit, but this is defensive) is also refused rather than
    silently trusted, since this module has no `verified`-style secondary
    trust signal to fall back on the way packages.py does."""
    if not expected_sha256:
        raise PypiError(
            f"{name!r} {version}: PyPI supplied no sha256 digest for this "
            "file -- refusing to install without one"
        )
    actual = _sha256_of_bytes(data)
    if actual.lower() != expected_sha256.lower():
        raise PypiError(
            f"{name!r} {version}: checksum mismatch — expected "
            f"{expected_sha256}, got {actual}. The download may be "
            "corrupted or tampered with; refusing to install."
        )


# ---------------------------------------------------------------------------
# Wheel inspection
# ---------------------------------------------------------------------------

def _inspect_wheel(data: bytes) -> WheelInfo:
    """Parse a wheel's METADATA + RECORD without extracting anything.
    Raises PypiError (naming the specific member paths) if the wheel
    contains a compiled extension module, or if it has no RECORD at all
    (a technically-nonconformant but real-world-occurring wheel shape --
    fail closed rather than fall back to enumerating the zip directly,
    since RECORD is PyPI's own authoritative "these are the real files"
    manifest)."""
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        dist_info = next((d for d in {n.split("/")[0] for n in names} if d.endswith(".dist-info")), None)
        if dist_info is None:
            raise PypiError("wheel has no *.dist-info/ directory -- not a valid wheel")

        record_path = f"{dist_info}/RECORD"
        if record_path not in names:
            raise PypiError(
                f"wheel's {dist_info}/RECORD is missing -- refusing to "
                "install a wheel without an authoritative file manifest"
            )
        record_text = zf.read(record_path).decode("utf-8", errors="replace")
        record_members = [
            line.split(",", 1)[0]
            for line in record_text.splitlines()
            if line.strip()
        ]

        metadata_path = f"{dist_info}/METADATA"
        meta_name = "unknown"
        meta_version = "0"
        requires_dist: list = []
        if metadata_path in names:
            meta_text = zf.read(metadata_path).decode("utf-8", errors="replace")
            msg = email.parser.Parser().parsestr(meta_text)
            meta_name = msg.get("Name", meta_name)
            meta_version = msg.get("Version", meta_version)
            requires_dist = msg.get_all("Requires-Dist") or []

        native_members = [
            m for m in record_members
            if any(m.lower().endswith(suf) for suf in _NATIVE_EXT_SUFFIXES)
        ]

        return WheelInfo(
            name=meta_name,
            version=meta_version,
            requires_dist=requires_dist,
            has_native_extension=bool(native_members),
            native_extension_members=native_members,
            record_members=record_members,
        )


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

def install_pypi_package(name: str, dest_dir: Path, *, version: "str | None" = None) -> "tuple[str, list[str]]":
    """Install *name* (optionally pinned to *version*) into *dest_dir* as
    an importable pure-Python package tree. Returns
    ``(resolved_version, [extracted filenames])``.

    Raises PypiError before extracting anything if: the package has no
    pure-Python wheel, the wheel contains a compiled extension, or the
    downloaded bytes don't match PyPI's own recorded sha256."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    meta = resolve_pypi_package(name, version)
    info = meta.get("info", {})
    resolved_version = info.get("version") or version
    urls = meta.get("urls", [])
    if not urls:
        raise PypiError(f"{name!r} {resolved_version}: no downloadable files listed on PyPI")

    wheel = _select_wheel(urls)
    data = _download_bytes(wheel["url"])
    _check_integrity(name, resolved_version, wheel.get("digests", {}).get("sha256"), data)

    wheel_info = _inspect_wheel(data)
    if wheel_info.has_native_extension:
        raise PypiError(
            f"{name!r} {resolved_version}: this wheel contains compiled "
            "extension module(s), which asmpython cannot load (neither "
            "the native backend nor pyinbin has a CPython-C-API-compatible "
            "extension loader): " + ", ".join(wheel_info.native_extension_members)
        )

    import io

    installed: list = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in wheel_info.record_members:
            if not member or member.endswith("/"):
                continue
            top = member.split("/", 1)[0]
            if top.endswith(".dist-info") or top.endswith(".data"):
                continue
            try:
                zf.extract(member, dest_dir)
            except KeyError:
                continue
            installed.append(member)

    manifest = _read_manifest(dest_dir)
    manifest[name.lower()] = {
        "name": name,
        "version": resolved_version,
        "files": installed,
    }
    _write_manifest(dest_dir, manifest)
    return resolved_version, installed


def uninstall_pypi_package(name: str, dest_dir: Path) -> list:
    """Remove a package `install_pypi_package` previously installed into
    *dest_dir*, using its own manifest entry. Returns the list of removed
    file paths (relative to *dest_dir*). No-op (returns []) if the
    package isn't recorded as installed there."""
    manifest = _read_manifest(dest_dir)
    key = name.lower()
    entry = manifest.get(key)
    if entry is None:
        return []

    removed: list = []
    for rel in entry.get("files", []):
        p = dest_dir / rel
        try:
            if p.is_file():
                p.unlink()
                removed.append(rel)
        except OSError:
            pass

    # Clean up now-empty directories the removed files left behind.
    dirs = sorted({(dest_dir / rel).parent for rel in entry.get("files", [])}, key=lambda p: -len(p.parts))
    for d in dirs:
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass

    del manifest[key]
    _write_manifest(dest_dir, manifest)
    return removed


def list_pypi_packages(dest_dir: Path) -> dict:
    """Return the manifest dict of everything installed into *dest_dir*
    (name -> {name, version, files})."""
    return _read_manifest(dest_dir)
