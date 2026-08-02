"""Packaged plugin formats: .apx / .apb / .apl / .apmlc / .apm.

Every one of these is a plain zip archive containing exactly one Python
entry file (plus, optionally, a small manifest declaring which file to run
and what kind of package it is). This module owns the one thing all five
formats share -- resolving a path to the plugin source text to exec -- plus
the two loaders (`load_mlang_config`, `load_module_package`) that have no
existing registry to diff against and so need their own logic beyond the
generic exec-and-diff pattern `_compiler/__main__.py`'s `_load_ext_plugin`/
`_load_backend_plugin`/`_load_linker_plugin` already use for the other three.

Format
------
A `.apx`/`.apb`/`.apl`/`.apmlc`/`.apm` file is any zip archive. Two shapes
are accepted:

  1. **Manifest-optional** (the common case): the zip contains exactly one
     top-level `*.py` file and nothing else load-bearing. That file is the
     entry point, exec'd exactly like a bare `--ext plugin.py` file is
     today -- zero ceremony, no JSON to hand-write for a single-file plugin.
  2. **Manifest-present**: a top-level `apkg.json` names the entry file
     explicitly (`entry`) and declares a `kind` (`"extension"` /
     `"backend"` / `"linker"` / `"mlang_config"` / `"module"`, corresponding
     to .apx/.apb/.apl/.apmlc/.apm). `kind` is advisory only -- used to
     improve an error message when a package is loaded through the wrong
     flag, never to gate what's allowed to register. Every loader validates
     by what actually got registered (content), exactly like the existing
     `--ext path.py` plugin loader already does, not by file extension or
     manifest `kind`.

There is no integrity/signature field (no sha256, unlike the downloaded-
archive packages `_compiler/packages.py` handles) -- these are local files a
user points a CLI flag at directly, not a download-and-cache pipeline with a
tampering-in-transit threat model to defend against.

There is no sandboxing: every entry file is `exec()`'d in the host CPython
process with full trust, identical to how a bare `--ext plugin.py` file is
loaded today. This is a deliberate, already-accepted gap (see
docs/EXTENSIONS.md's "Security and reproducibility" section) -- these new
formats change *packaging*, not the trust model.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


MANIFEST_NAME = "apkg.json"
FORMAT_TAG = "asmpython.apkg"
FORMAT_VERSION = 1

_VALID_KINDS = {"extension", "backend", "linker", "mlang_config", "module"}


class ApkgError(Exception):
    """Raised for any malformed or ambiguous .apx/.apb/.apl/.apmlc/.apm file."""


def read_entry_source(path: Path) -> tuple[str, str, "str | None"]:
    """Resolve `path` to (source_text, display_name, kind).

    `path` may be a zip archive (.apx/.apb/.apl/.apmlc/.apm) or a bare `.py`
    file -- the latter preserves the exact behavior `--ext plugin.py` has
    always had, so existing plugin files keep working unchanged. `kind` is
    the manifest's advisory `kind` field, or None (no manifest, or a bare
    `.py` file).
    """
    if not zipfile.is_zipfile(path):
        return path.read_text(encoding="utf-8"), str(path), None

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        kind: "str | None" = None
        entry_name: "str | None" = None

        if MANIFEST_NAME in names:
            try:
                manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                raise ApkgError(f"{path}: malformed {MANIFEST_NAME}: {e}") from e
            if manifest.get("format") != FORMAT_TAG or manifest.get("version") != FORMAT_VERSION:
                raise ApkgError(
                    f"{path}: {MANIFEST_NAME} has format={manifest.get('format')!r} "
                    f"version={manifest.get('version')!r}, expected "
                    f"format={FORMAT_TAG!r} version={FORMAT_VERSION!r}"
                )
            entry_name = manifest.get("entry")
            if not entry_name:
                raise ApkgError(f"{path}: {MANIFEST_NAME} is missing required field 'entry'")
            if entry_name not in names:
                raise ApkgError(f"{path}: manifest names entry {entry_name!r}, not found in archive")
            kind = manifest.get("kind")
            if kind is not None and kind not in _VALID_KINDS:
                raise ApkgError(
                    f"{path}: {MANIFEST_NAME} has unrecognised kind {kind!r} "
                    f"(expected one of {sorted(_VALID_KINDS)})"
                )
        else:
            candidates = [
                n for n in names
                if n.endswith(".py") and "/" not in n.rstrip("/")
            ]
            if len(candidates) == 0:
                raise ApkgError(
                    f"{path}: no {MANIFEST_NAME} and no top-level *.py file found"
                )
            if len(candidates) > 1:
                raise ApkgError(
                    f"{path}: no {MANIFEST_NAME} and multiple top-level *.py files "
                    f"found ({', '.join(sorted(candidates))}) -- add {MANIFEST_NAME} "
                    f"to disambiguate the entry file"
                )
            entry_name = candidates[0]

        try:
            source = zf.read(entry_name).decode("utf-8")
        except UnicodeDecodeError as e:
            raise ApkgError(f"{path}: entry file {entry_name!r} is not valid UTF-8: {e}") from e
        return source, f"{path}::{entry_name}", kind


def _exec_entry(path: Path) -> dict:
    """Read and exec `path`'s entry file, returning the resulting namespace."""
    source, display_name, _kind = read_entry_source(path)
    ns: dict = {"__name__": f"asmpython_apkg_{path.stem}", "__file__": str(path)}
    try:
        exec(compile(source, display_name, "exec"), ns)
    except Exception as e:
        raise ApkgError(f"failed to load {path}: {e}") from e
    return ns


def load_mlang_config(path: Path):
    """Load a `.apmlc` package and return the single `asmpython.mlang.Config`
    it constructs. Unlike Extension/Backend/Linker, Config has no
    registration side effect (it's a plain frozen dataclass), so there is no
    registry to diff -- this scans the exec namespace directly for the one
    Config instance it must contain."""
    from asmpython.mlang import Config

    ns = _exec_entry(path)
    configs = [v for v in ns.values() if isinstance(v, Config)]
    if len(configs) == 0:
        raise ApkgError(f"{path}: did not construct any asmpython.mlang.Config(...)")
    if len(configs) > 1:
        raise ApkgError(
            f"{path}: constructed {len(configs)} asmpython.mlang.Config(...) instances, "
            f"expected exactly one"
        )
    return configs[0]


@dataclass
class ApmLoadResult:
    extension_ids: list = field(default_factory=list)
    backend_names: list = field(default_factory=list)
    linker_names: list = field(default_factory=list)
    mlang_configs: list = field(default_factory=list)
    ran_on_load: bool = False


def load_module_package(path: Path) -> ApmLoadResult:
    """Load a `.apm` bundle: any combination of Extension/Backend/Linker
    registrations plus mlang Config instances, from one exec of the entry
    file, plus an optional `on_load(asmpython)` behavior-modification hook.

    Unlike the single-purpose loaders (exactly one registration required),
    a .apm package may register zero or more of each kind -- the only hard
    requirement is that the package does *something* (at least one
    registration, one Config, or a real on_load hook), since a package that
    registers nothing and has no on_load is simply useless.
    """
    import asmpython
    from asmpython._compiler import extensions as _extensions
    from asmpython import _backends, _linkers
    from asmpython.mlang import Config

    ext_before = set(_extensions._REGISTRY.keys())
    backend_before = set(_backends._REGISTRY.keys())
    linker_before = set(_linkers._REGISTRY.keys())

    ns = _exec_entry(path)

    ext_after = set(_extensions._REGISTRY.keys())
    backend_after = set(_backends._REGISTRY.keys())
    linker_after = set(_linkers._REGISTRY.keys())

    result = ApmLoadResult(
        extension_ids=sorted(ext_after - ext_before),
        backend_names=sorted(backend_after - backend_before),
        linker_names=sorted(linker_after - linker_before),
        mlang_configs=[v for v in ns.values() if isinstance(v, Config)],
    )

    on_load = ns.get("on_load")
    if callable(on_load):
        on_load(asmpython)
        result.ran_on_load = True

    if not (
        result.extension_ids
        or result.backend_names
        or result.linker_names
        or result.mlang_configs
        or result.ran_on_load
    ):
        raise ApkgError(
            f"{path}: registered nothing -- expected at least one "
            f"Extension/Backend/Linker/Config or an on_load(asmpython) hook"
        )
    return result
