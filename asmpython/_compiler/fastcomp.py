"""Incremental native compilation and fragment cache.

For the current x86-64 NASM backends, fastcomp records the generated assembly
range for each user function/method, assembles each range into its own object,
and links those objects with a separately cached base/runtime/data object.
Unchanged assembly fragments therefore keep their previous .o/.obj binaries.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MethodType
from typing import Any

from .. import __version__
from .driver import (
    BuildResult,
    _build_icon_resource,
    _resolve_tool,
    _run,
    _run_backend,
    _shared_lib_path,
)
from .irfreeze import FrozenIR, component_hashes, dump_ir, load_ir
from .target_linux import LinuxCodegen
from .target_windows import WindowsCodegen

CACHE_SCHEMA = 1
_LABEL_RE = re.compile(r"^\s*([A-Za-z_?$@][A-Za-z0-9_?$@.]*)\s*:")
_EXTERN_RE = re.compile(r"^\s*extern\s+([^\s;]+)")
_GLOBAL_RE = re.compile(r"^\s*global\s+([^\s;]+)")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class AssemblyFragment:
    name: str
    source: str
    digest: str


def default_cache_dir() -> Path:
    override = os.environ.get("ASMPYTHON_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "asmpython" / "cache"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "asmpython"


def invalidate_cache(*, cache_dir: Path | None = None, source: Path | None = None) -> int:
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    if not root.exists():
        return 0
    if source is None:
        count = sum(1 for path in root.iterdir() if path.is_dir())
        shutil.rmtree(root)
        return count

    source_value = str(Path(source).resolve())
    removed = 0
    for child in list(root.iterdir()):
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.get("source_path") == source_value:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    if root.exists() and not any(root.iterdir()):
        root.rmdir()
    return removed


def _frontend_cache_key(source_path: Path) -> str:
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

    pending = [source_path.resolve()]
    seen: set[Path] = set()
    hashes: dict[str, str] = {}
    while pending:
        path = pending.pop()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        raw = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(raw).hexdigest()
        try:
            tree = ast.parse(raw.decode("utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolved = _resolve_local_import(path.parent, alias.name, 0)
                    if resolved is not None:
                        pending.append(resolved)
            elif isinstance(node, ast.ImportFrom):
                resolved = _resolve_local_import(
                    path.parent, node.module or "", int(node.level or 0)
                )
                if resolved is not None:
                    pending.append(resolved)
    return dict(sorted(hashes.items()))


def load_cached_frontend(
    source_path: Path, *, cache_dir: Path | None = None
) -> Any | None:
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    directory = root / _frontend_cache_key(source_path)
    manifest_path = directory / "manifest.json"
    ir_path = directory / "module.apir"
    manifest = _load_manifest(manifest_path)
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
    directory = root / _frontend_cache_key(source_path)
    directory.mkdir(parents=True, exist_ok=True)
    dependencies = dependency_snapshot(source_path)
    metadata = {
        "format": "asmpython-ir",
        "format_version": 1,
        "compiler_version": __version__,
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


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_key(source_path: Path, target: str, output_type: str, config: dict[str, Any]) -> str:
    document = {
        "source_path": str(source_path.resolve()),
        "target": target,
        "output_type": output_type,
        "compiler_version": __version__,
        "config": config,
    }
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def _defined_labels(lines: list[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        match = _LABEL_RE.match(line)
        if match and not match.group(1).startswith("."):
            out.add(match.group(1))
    return out


def _declared(lines: list[str], regex: re.Pattern[str]) -> set[str]:
    out: set[str] = set()
    for line in lines:
        match = regex.match(line)
        if match:
            out.add(match.group(1))
    return out


def _insert_before_first_section(lines: list[str], declarations: list[str]) -> list[str]:
    if not declarations:
        return list(lines)
    index = len(lines)
    for i, line in enumerate(lines):
        if line.lstrip().startswith("section "):
            index = i
            break
    return [*lines[:index], *declarations, *lines[index:]]


def _common_directives(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith("BITS ")
            or stripped.startswith("default ")
            or stripped.startswith("%define ")
            or stripped.startswith("%include ")
            or stripped.startswith("%assign ")
        ):
            out.append(line)
    return out


def _recorded_codegen(gen: Any) -> tuple[list[str], list[tuple[str, int, int]]]:
    records: list[tuple[str, int, int]] = []
    original = gen.emit_function

    def wrapped(_self: Any, func: Any) -> None:
        start = len(gen.lines)
        symbol = getattr(func, "asm_symbol", None) or gen._user_symbol(func.name)
        original(func)
        records.append((str(symbol), start, len(gen.lines)))

    gen.emit_function = MethodType(wrapped, gen)
    gen.generate()
    return list(gen.lines), records


def _make_fragment_sources(
    lines: list[str], records: list[tuple[str, int, int]]
) -> list[AssemblyFragment]:
    """Split full NASM into independently assemblable base/function units."""

    mask = [False] * len(lines)
    bodies: list[tuple[str, list[str]]] = []
    for name, start, end in records:
        for index in range(start, end):
            mask[index] = True
        bodies.append((name, lines[start:end]))
    base_lines = [line for index, line in enumerate(lines) if not mask[index]]

    base_defined = _defined_labels(base_lines)
    function_defined: set[str] = set()
    body_defined: dict[str, set[str]] = {}
    for name, body in bodies:
        labels = _defined_labels(body)
        labels.add(name)
        body_defined[name] = labels
        function_defined.update(labels)

    all_defined = base_defined | function_defined
    original_externs = _declared(lines, _EXTERN_RE)
    base_globals = _declared(base_lines, _GLOBAL_RE)
    base_externs = _declared(base_lines, _EXTERN_RE)
    base_decls = [
        f"global {name}"
        for name in sorted(base_defined - base_globals - original_externs)
    ]
    base_decls += [
        f"extern {name}"
        for name in sorted(function_defined - base_defined - base_externs)
    ]
    rendered_base = _insert_before_first_section(base_lines, base_decls)

    fragments: list[AssemblyFragment] = []
    base_source = "\n".join(rendered_base) + "\n"
    fragments.append(AssemblyFragment("__base__", base_source, _hash_text(base_source)))

    directives = _common_directives(lines)
    for name, body in bodies:
        own = body_defined[name]
        declarations = [f"global {label}" for label in sorted(own)]
        declarations += [
            f"extern {label}"
            for label in sorted((all_defined | original_externs) - own)
        ]
        source_lines = [*directives, *declarations, "section .text", *body]
        source = "\n".join(source_lines) + "\n"
        fragments.append(AssemblyFragment(name, source, _hash_text(source)))
    return fragments


def _safe_fragment_name(index: int, name: str) -> str:
    clean = _SAFE_NAME_RE.sub("_", name).strip("._") or "fragment"
    return f"{index:04d}-{clean}"


def _load_manifest(path: Path) -> dict[str, Any]:
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


def _select_codegen(module: Any, target: str, use_runtime_lib: bool) -> tuple[Any, str, str]:
    if target == "linux":
        return LinuxCodegen(module, use_runtime_lib=use_runtime_lib), "elf64", ".o"
    if target == "windows":
        return WindowsCodegen(module, use_runtime_lib=use_runtime_lib), "win64", ".obj"
    raise ValueError(f"fastcomp fragmentation is not available for target {target!r}")


def fast_compile_module(
    module: Any,
    target: str,
    out_path: Path,
    *,
    source_path: Path,
    cache_dir: Path | None = None,
    keep_assembly: bool = False,
    use_runtime_lib: bool = False,
    nasm_path: Path | None = None,
    gcc_path: Path | None = None,
    bundle_mode: str = "onefile",
    output_type: str = "executable",
    icon_path: Path | None = None,
) -> BuildResult:
    """Compile a typed module, reassembling only changed NASM fragments."""

    if target not in {"linux", "windows"} or bundle_mode != "onefile":
        print(
            f"asmpython: fastcomp: {target}/{bundle_mode} does not support fragment "
            "stitching yet; using the normal compiler",
            file=sys.stderr,
        )
        return _run_backend(
            module,
            target,
            out_path,
            keep_assembly=keep_assembly,
            use_runtime_lib=use_runtime_lib,
            nasm_path=nasm_path,
            gcc_path=gcc_path,
            bundle_mode=bundle_mode,
            output_type=output_type,
            icon_path=icon_path,
        )

    gen, nasm_format, object_suffix = _select_codegen(module, target, use_runtime_lib)
    lines, ranges = _recorded_codegen(gen)
    fragments = _make_fragment_sources(lines, ranges)

    icon_digest = None
    if icon_path is not None and Path(icon_path).is_file():
        icon_digest = hashlib.sha256(Path(icon_path).read_bytes()).hexdigest()
    config = {
        "bundle_mode": bundle_mode,
        "use_runtime_lib": use_runtime_lib,
        "nasm_format": nasm_format,
        "icon": str(Path(icon_path).resolve()) if icon_path is not None else None,
        "icon_digest": icon_digest,
    }
    root = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    build_cache = root / _cache_key(source_path, target, output_type, config)
    build_cache.mkdir(parents=True, exist_ok=True)
    manifest_path = build_cache / "manifest.json"
    old = _load_manifest(manifest_path)
    old_fragments = old.get("fragments", {}) if old else {}

    nasm = _resolve_tool("nasm", override=nasm_path, env_var="ASMPYTHON_NASM")
    fragment_manifest: dict[str, Any] = {}
    object_paths: list[Path] = []
    changed: list[str] = []
    live_files: set[str] = {"manifest.json"}

    for index, fragment in enumerate(fragments):
        safe_name = _safe_fragment_name(index, fragment.name)
        asm_path = build_cache / f"{safe_name}.asm"
        obj_path = build_cache / f"{safe_name}{object_suffix}"
        live_files.add(obj_path.name)
        if keep_assembly:
            live_files.add(asm_path.name)
        previous = old_fragments.get(fragment.name, {})
        needs_assembly = previous.get("digest") != fragment.digest or not obj_path.is_file()
        if needs_assembly:
            asm_path.write_text(fragment.source, encoding="utf-8")
            _run([
                nasm,
                "-f",
                nasm_format,
                "-w-label-redef-late",
                str(asm_path),
                "-o",
                str(obj_path),
            ])
            changed.append(fragment.name)
        if not keep_assembly:
            try:
                asm_path.unlink()
            except OSError:
                pass
        fragment_manifest[fragment.name] = {
            "digest": fragment.digest,
            "object": obj_path.name,
        }
        object_paths.append(obj_path)

    for child in build_cache.iterdir():
        if (
            child.is_file()
            and child.name not in live_files
            and child.suffix in {".o", ".obj", ".asm"}
        ):
            child.unlink()

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not changed and out_path.exists():
        print(f"fastcomp: up to date ({len(fragments)} cached fragments)")
    else:
        gcc = _resolve_tool("gcc", override=gcc_path, env_var="ASMPYTHON_GCC")
        gcc_dir = str(Path(gcc).parent)
        icon_obj: Path | None = None
        if icon_path is not None:
            if target != "windows":
                print(
                    "asmpython: --icon is only supported for Windows; ignoring it",
                    file=sys.stderr,
                )
            else:
                icon_obj = _build_icon_resource(icon_path, out_path.with_suffix(""), gcc)

        if output_type == "library":
            out_path = _shared_lib_path(out_path, target)
            link_cmd = [gcc, "-shared", *map(str, object_paths)]
            if icon_obj is not None:
                link_cmd.append(str(icon_obj))
            link_cmd += ["-o", str(out_path)]
            if target == "windows":
                link_cmd += ["-Wl,--export-all-symbols"]
        else:
            link_cmd = [gcc, *map(str, object_paths)]
            if icon_obj is not None:
                link_cmd.append(str(icon_obj))
            link_cmd += ["-o", str(out_path)]
            if target == "linux":
                link_cmd.append("-no-pie")
            else:
                link_cmd.append("-mconsole")

        if use_runtime_lib:
            from .._runtime.build import _build_dir, build_runtime

            build_runtime(target)
            link_cmd += [
                f"-L{_build_dir()}",
                f"-lasmpython_rt_{'win' if target == 'windows' else 'linux'}",
            ]
        if target == "windows" and getattr(gen, "needs_net", False):
            link_cmd.append("-lws2_32")
        if target == "linux" and any(
            symbol in getattr(gen, "ffi_externs", set())
            for symbol in getattr(gen, "_THREAD_SYMS", ())
        ):
            link_cmd.append("-lpthread")
        if getattr(gen, "needs_gui", False):
            link_cmd.append("-lSDL2")
        if getattr(gen, "needs_audio", False):
            link_cmd.append("-lSDL2_mixer")
        if getattr(gen, "needs_ttf", False):
            link_cmd.append("-lSDL2_ttf")
        _run(link_cmd, extra_path_dirs=[gcc_dir])
        print(
            "fastcomp: assembled "
            + (", ".join(changed) if changed else "no fragments")
            + f"; stitched {len(object_paths)} cached object fragments"
        )
        print(f"wrote {out_path}")

    document = {
        "schema": CACHE_SCHEMA,
        "compiler_version": __version__,
        "source_path": str(source_path.resolve()),
        "target": target,
        "output_type": output_type,
        "config": config,
        "fragments": fragment_manifest,
    }
    _write_manifest(manifest_path, document)
    base_asm = build_cache / "0000-base.asm"
    return BuildResult(
        asm_path=base_asm,
        obj_path=object_paths[0] if object_paths else None,
        exe_path=out_path,
    )
