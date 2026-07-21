"""Dependency-safe frozen front-end cache used by ``--fastcomp``."""

from __future__ import annotations

import ast
import hashlib
import json
import marshal
import os
import re
import sys
from pathlib import Path
from typing import Any

from .. import __version__
from .irfreeze import FrozenIR, component_hashes, dump_ir, load_ir

CACHE_SCHEMA = 1


def default_cache_dir() -> Path:
    override = os.environ.get("ASMPYTHON_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "asmpython" / "cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "asmpython"


def _cache_key(source_path: Path) -> str:
    raw = (str(source_path.resolve()) + "\0" + __version__).encode("utf-8")
    return "frontend-" + hashlib.sha256(raw).hexdigest()[:32]


def _resolve_local_import(base: Path, module: str, level: int = 0) -> Path | None:
    root = base
    if level > 0:
        for _ in range(max(0, level - 1)):
            root = root.parent
    parts = [part for part in module.split(".") if part]
    candidate = root.joinpath(*parts) if parts else root
    file_candidate = candidate.with_suffix(".py")
    if file_candidate.is_file():
        return file_candidate.resolve()
    init_candidate = candidate / "__init__.py"
    if init_candidate.is_file():
        return init_candidate.resolve()
    return None


def dependency_snapshot(source_path: Path) -> dict[str, str]:
    """Hash the entry source and recursively resolvable project-local imports."""

    entry = source_path.resolve()
    project_root = entry.parent
    pending = [entry]
    seen: set[Path] = set()
    hashes: dict[str, str] = {}

    def queue_module(base: Path, module: str, level: int = 0) -> None:
        candidates = [base]
        if level == 0 and project_root != base:
            candidates.append(project_root)
        for candidate_base in candidates:
            resolved = _resolve_local_import(candidate_base, module, level)
            if resolved is not None:
                pending.append(resolved)
                return

    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            # ASMPython extensions can be valid while CPython rejects them.
            # A conservative import scan avoids silently reusing stale IR.
            for line in text.splitlines():
                import_match = re.match(r"^\s*import\s+([A-Za-z_][\w.]*)", line)
                if import_match:
                    queue_module(path.parent, import_match.group(1), 0)
                    continue
                from_match = re.match(
                    r"^\s*from\s+([.]*)([A-Za-z_][\w.]*)?\s+import\s+(.+)",
                    line,
                )
                if from_match:
                    dots, module, names = from_match.groups()
                    level = len(dots)
                    queue_module(path.parent, module or "", level)
                    if not module:
                        for name in names.split(","):
                            clean = name.strip().split()[0]
                            if clean and clean != "*":
                                queue_module(path.parent, clean, level)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    queue_module(path.parent, alias.name, 0)
            elif isinstance(node, ast.ImportFrom):
                level = int(node.level or 0)
                module_name = node.module or ""
                queue_module(path.parent, module_name, level)
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    child = f"{module_name}.{alias.name}" if module_name else alias.name
                    queue_module(path.parent, child, level)
    return dict(sorted(hashes.items()))


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if document.get("schema") != CACHE_SCHEMA:
        return {}
    return document


def _write_manifest(path: Path, document: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def load_cached_frontend(
    source_path: Path, *, cache_dir: Path | None = None
) -> Any | None:
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    directory = root / _cache_key(source_path)
    manifest = _read_manifest(directory / "manifest.json")
    ir_path = directory / "module.apir"
    if not manifest or not ir_path.is_file():
        return None
    if manifest.get("kind") != "frontend" or manifest.get("compiler_version") != __version__:
        return None
    if manifest.get("dependencies") != dependency_snapshot(source_path):
        return None
    try:
        return load_ir(ir_path).module
    except (OSError, ValueError, TypeError):
        return None


def store_cached_frontend(
    module: Any, source_path: Path, *, cache_dir: Path | None = None
) -> Path:
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    directory = root / _cache_key(source_path)
    directory.mkdir(parents=True, exist_ok=True)
    dependencies = dependency_snapshot(source_path)
    metadata = {
        "format": "asmpython-ir",
        "format_version": 1,
        "compiler_version": __version__,
        "python_cache_tag": sys.implementation.cache_tag,
        "marshal_version": marshal.version,
        "stage": "optimized",
        "source_path": str(source_path.resolve()),
        "source_sha256": dependencies.get(str(source_path.resolve()), ""),
        "passes": ["semantic-analysis", "canonical-typed-ir"],
        "components": component_hashes(module),
    }
    ir_path = directory / "module.apir"
    dump_ir(FrozenIR(module=module, metadata=metadata), ir_path, output="bin")
    _write_manifest(
        directory / "manifest.json",
        {
            "schema": CACHE_SCHEMA,
            "kind": "frontend",
            "compiler_version": __version__,
            "source_path": str(source_path.resolve()),
            "dependencies": dependencies,
        },
    )
    return ir_path
