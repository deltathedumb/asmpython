"""Bridge the shared FastComp state cache to native object-fragment stitching.

The frontend cache and the NASM fragment cache intentionally remain separate
layers under the same ASMPython cache root:

- ``fast-state/<key>`` stores parsed modules, dependency graphs, typed IR, and a
  serializable pointer to backend state;
- ``<fragment-key>`` stores generated assembly fragments and their assembled
  ``.o``/``.obj`` files.

This module installs a narrow driver hook for the legacy x86 NASM backend. Other
backends receive ``fastcomp`` and ``fastcomp_state_path`` and may implement their
own incremental state without being forced through the legacy stitcher.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import driver
from .build_options import (
    active_sanitizers,
    debug_enabled,
    fastcomp_enabled,
)
from .build_report import event, stage
from .fastcomp import fast_compile_module
from .fast_state import FastState, store_backend_state, store_ir


_original_run_backend = driver._run_backend


def _state_from_environment() -> FastState | None:
    raw = os.environ.get("ASMPYTHON_FAST_STATE")
    if not raw:
        return None
    directory = Path(raw)
    try:
        manifest = json.loads(
            (directory / "manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    source_text = manifest.get("source")
    dependencies = manifest.get("dependencies")
    if not isinstance(source_text, str) or not isinstance(dependencies, dict):
        return None
    graph: dict[str, list[str]] = {}
    try:
        candidate = json.loads(
            (directory / "dependency-graph.json").read_text(encoding="utf-8")
        )
        if isinstance(candidate, dict):
            graph = candidate
    except (OSError, ValueError):
        pass
    return FastState(
        key=directory.name,
        directory=directory,
        hit=True,
        source=Path(source_text),
        dependencies={str(key): str(value) for key, value in dependencies.items()},
        graph=graph,
        parsed_modules={},
    )


def _fragment_cache_root(state: FastState) -> Path:
    # default fast-state path: <cache-root>/fast-state/<key>
    if state.directory.parent.name == "fast-state":
        return state.directory.parent.parent
    return state.directory.parent


def _can_stitch(
    *,
    target: str,
    backend: str,
    linker: str | None,
    emit_asm_only: bool,
    keep_intermediates: bool,
    bundle_mode: str,
    passes: str | None = None,
) -> tuple[bool, str | None]:
    if not fastcomp_enabled():
        return False, None
    if backend != "legacy":
        return False, "selected backend owns its own FastComp implementation"
    if passes:
        # Fragment stitching is an AST->NASM-text path with no IRModule for an
        # IR->IR pass to transform. Defer to the ordinary driver, which reports
        # the --passes/--backend legacy combination as the error it is.
        return False, "--passes requires an IR backend, not fragment stitching"
    if target not in {"windows", "linux"}:
        return False, f"legacy fragment stitching does not support target {target!r}"
    if bundle_mode != "onefile":
        return False, "legacy fragment stitching currently requires onefile output"
    if emit_asm_only or keep_intermediates:
        return False, "intermediate-output modes use the normal compiler"
    if linker not in {None, "gcc"}:
        return False, f"legacy fragment stitching requires GCC, not {linker!r}"
    if active_sanitizers():
        return False, "sanitized builds use the ordinary GCC pipeline"
    if debug_enabled():
        return False, "debug builds use the ordinary GCC pipeline"
    return True, None


def _run_backend_fastcomp(
    module: Any,
    target: str,
    out_path: Path,
    *,
    emit_asm_only: bool = False,
    keep_intermediates: bool = False,
    keep_assembly: bool = False,
    use_runtime_lib: bool = False,
    nasm_path: Path | None = None,
    gcc_path: Path | None = None,
    bundle_mode: str = "onefile",
    output_type: str = "executable",
    icon_path: Path | None = None,
    entry_path: Path | None = None,
    backend: str = "legacy",
    linker: str | None = None,
    passes: str | None = None,
    _asm_stem_suffix: str = "",
):
    allowed, reason = _can_stitch(
        target=target,
        backend=backend,
        linker=linker,
        emit_asm_only=emit_asm_only,
        keep_intermediates=keep_intermediates,
        bundle_mode=bundle_mode,
        passes=passes,
    )
    if not allowed:
        if fastcomp_enabled() and reason:
            event("fastcomp.fragment-fallback", reason=reason)
        return _original_run_backend(
            module,
            target,
            out_path,
            emit_asm_only=emit_asm_only,
            keep_intermediates=keep_intermediates,
            keep_assembly=keep_assembly,
            use_runtime_lib=use_runtime_lib,
            nasm_path=nasm_path,
            gcc_path=gcc_path,
            bundle_mode=bundle_mode,
            output_type=output_type,
            icon_path=icon_path,
            entry_path=entry_path,
            backend=backend,
            linker=linker,
            passes=passes,
            _asm_stem_suffix=_asm_stem_suffix,
        )

    state = _state_from_environment()
    source_path = entry_path.resolve() if entry_path is not None else None
    if source_path is None and state is not None:
        source_path = state.source.resolve()
    if source_path is None:
        event(
            "fastcomp.fragment-fallback",
            reason="source path was unavailable for the fragment cache key",
        )
        return _original_run_backend(
            module,
            target,
            out_path,
            emit_asm_only=emit_asm_only,
            keep_intermediates=keep_intermediates,
            keep_assembly=keep_assembly,
            use_runtime_lib=use_runtime_lib,
            nasm_path=nasm_path,
            gcc_path=gcc_path,
            bundle_mode=bundle_mode,
            output_type=output_type,
            icon_path=icon_path,
            entry_path=entry_path,
            backend=backend,
            linker=linker,
            passes=passes,
            _asm_stem_suffix=_asm_stem_suffix,
        )

    cache_root = _fragment_cache_root(state) if state is not None else None
    if state is not None:
        try:
            store_ir(state, module)
        except (OSError, TypeError, ValueError):
            # The compiler must still build when an extension attaches a
            # non-pickleable host object to the typed module.
            event(
                "fastcomp.ir-cache-skipped",
                reason="typed module is not serializable",
            )

    with stage(
        "fastcomp.fragment-stitch",
        source=source_path,
        target=target,
        cache_root=cache_root,
    ):
        result = fast_compile_module(
            module,
            target,
            out_path,
            source_path=source_path,
            cache_dir=cache_root,
            keep_assembly=keep_assembly,
            use_runtime_lib=use_runtime_lib,
            nasm_path=nasm_path,
            gcc_path=gcc_path,
            bundle_mode=bundle_mode,
            output_type=output_type,
            icon_path=icon_path,
        )

    if state is not None:
        try:
            store_backend_state(
                state,
                {
                    "kind": "legacy-nasm-fragments",
                    "cache_root": None if cache_root is None else str(cache_root),
                    "target": target,
                    "output_type": output_type,
                    "output": str(result.exe_path),
                },
            )
        except (OSError, TypeError, ValueError):
            event(
                "fastcomp.backend-state-skipped",
                reason="backend state is not serializable",
            )
    return result


def install() -> None:
    if driver._run_backend is not _run_backend_fastcomp:
        driver._run_backend = _run_backend_fastcomp


install()


__all__ = ["install"]
