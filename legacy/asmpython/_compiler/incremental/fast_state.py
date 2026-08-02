"""Persistent FastComp state for parsed modules, IR, dependency graphs, and backends."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import pickle
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from asmpython._version import FULL_VERSION


STATE_FORMAT = "asmpython.fast-state"
STATE_VERSION = 1


class FastStateError(RuntimeError):
    pass


@dataclass
class FastState:
    key: str
    directory: Path
    hit: bool
    source: Path
    dependencies: dict[str, str]
    graph: dict[str, list[str]]
    parsed_modules: dict[str, ast.AST]
    backend_state: Any = None
    ir: Any = None


def default_state_dir() -> Path:
    override = os.environ.get("ASMPYTHON_CACHE_DIR")
    if override:
        return Path(override).expanduser() / "fast-state"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "asmpython" / "fast-state"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_module(base: Path, project_root: Path, module: str, level: int) -> Path | None:
    roots: list[Path] = []
    if level:
        root = base
        for _ in range(max(0, level - 1)):
            root = root.parent
        roots.append(root)
    else:
        roots.extend((base, project_root))
    parts = [part for part in module.split(".") if part]
    for root in roots:
        candidate = root.joinpath(*parts) if parts else root
        py = candidate.with_suffix(".py")
        if py.is_file():
            return py.resolve()
        init = candidate / "__init__.py"
        if init.is_file():
            return init.resolve()
    return None


def _fallback_imports(text: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for line in text.splitlines():
        match = re.match(r"^\s*import\s+([A-Za-z_][\w.]*)", line)
        if match:
            result.append((match.group(1), 0))
            continue
        match = re.match(r"^\s*from\s+([.]*)([A-Za-z_][\w.]*)?\s+import\s+", line)
        if match:
            result.append((match.group(2) or "", len(match.group(1))))
    return result


def scan_project(source: Path) -> tuple[dict[str, str], dict[str, list[str]], dict[str, ast.AST]]:
    entry = source.resolve()
    project_root = entry.parent
    pending = [entry]
    seen: set[Path] = set()
    dependencies: dict[str, str] = {}
    graph: dict[str, list[str]] = {}
    parsed: dict[str, ast.AST] = {}

    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        key = str(path)
        dependencies[key] = _sha256(path)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            graph[key] = []
            continue
        imports: list[tuple[str, int]] = []
        try:
            tree = ast.parse(text, filename=key)
        except SyntaxError:
            tree = None
            imports = _fallback_imports(text)
        else:
            parsed[key] = tree
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend((alias.name, 0) for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.append((node.module or "", int(node.level or 0)))
        edges: list[str] = []
        for module, level in imports:
            resolved = _resolve_module(path.parent, project_root, module, level)
            if resolved is None:
                continue
            edges.append(str(resolved))
            pending.append(resolved)
        graph[key] = sorted(set(edges))
    return dict(sorted(dependencies.items())), dict(sorted(graph.items())), parsed


def _key(source: Path, *, backend: str | None, target: str | None) -> str:
    raw = json.dumps(
        {
            "source": str(source.resolve()),
            "backend": backend,
            "target": target,
            "release": FULL_VERSION,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:40]


def _atomic_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_pickle(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        pickle.dump(payload, stream, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(path)


def _manifest(directory: Path) -> dict[str, Any]:
    try:
        payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if payload.get("format") != STATE_FORMAT or payload.get("format_version") != STATE_VERSION:
        return {}
    return payload


def prepare_state(
    source: Path,
    *,
    backend: str | None = None,
    target: str | None = None,
    cache_dir: Path | None = None,
) -> FastState:
    source = source.resolve()
    root = cache_dir or default_state_dir()
    key = _key(source, backend=backend, target=target)
    directory = root / key
    current_dependencies, current_graph, current_parsed = scan_project(source)
    manifest = _manifest(directory)
    hit = bool(
        manifest
        and manifest.get("release") == FULL_VERSION
        and manifest.get("dependencies") == current_dependencies
    )
    if hit:
        try:
            with (directory / "parsed-modules.pkl").open("rb") as stream:
                parsed = pickle.load(stream)
            graph = json.loads((directory / "dependency-graph.json").read_text(encoding="utf-8"))
            backend_state = None
            backend_path = directory / "backend-state.pkl"
            if backend_path.is_file():
                with backend_path.open("rb") as stream:
                    backend_state = pickle.load(stream)
            ir = None
            ir_path = directory / "optimized-ir.pkl"
            if ir_path.is_file():
                with ir_path.open("rb") as stream:
                    ir = pickle.load(stream)
            return FastState(
                key, directory, True, source, current_dependencies, graph, parsed,
                backend_state=backend_state, ir=ir,
            )
        except (OSError, ValueError, TypeError, pickle.PickleError):
            hit = False

    directory.mkdir(parents=True, exist_ok=True)
    _atomic_pickle(directory / "parsed-modules.pkl", current_parsed)
    _atomic_json(directory / "dependency-graph.json", current_graph)
    _atomic_json(
        directory / "manifest.json",
        {
            "format": STATE_FORMAT,
            "format_version": STATE_VERSION,
            "release": FULL_VERSION,
            "source": str(source),
            "backend": backend,
            "target": target,
            "dependencies": current_dependencies,
            "updated_at": time.time(),
        },
    )
    return FastState(key, directory, False, source, current_dependencies, current_graph, current_parsed)


def store_ir(state: FastState, ir: Any) -> Path:
    path = state.directory / "optimized-ir.pkl"
    _atomic_pickle(path, ir)
    state.ir = ir
    return path


def store_backend_state(state: FastState, backend_state: Any) -> Path:
    path = state.directory / "backend-state.pkl"
    _atomic_pickle(path, backend_state)
    state.backend_state = backend_state
    return path


def state_summary(state: FastState) -> dict[str, Any]:
    return {
        "key": state.key,
        "path": str(state.directory),
        "hit": state.hit,
        "source": str(state.source),
        "dependencies": len(state.dependencies),
        "parsed_modules": len(state.parsed_modules),
        "dependency_edges": sum(len(items) for items in state.graph.values()),
        "has_ir": state.ir is not None,
        "has_backend_state": state.backend_state is not None,
    }


__all__ = [
    "FastState",
    "FastStateError",
    "default_state_dir",
    "prepare_state",
    "scan_project",
    "state_summary",
    "store_backend_state",
    "store_ir",
]
