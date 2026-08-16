"""Extended build negotiation for debugger-aware and delegated linkers."""
from __future__ import annotations

import os
from dataclasses import replace

from .build_options import (
    active_debug_format,
    active_sanitizers,
    debug_enabled,
    speedy_lossy_enabled,
)
from .capability_negotiation import (
    ComponentResult,
    NegotiationResult,
    _option_value,
    negotiate_component,
    resolve_backend,
    resolve_linker,
)


def _resolved_debug_format(target: str) -> str | None:
    if not debug_enabled():
        return None
    selected = active_debug_format().lower()
    if selected != "auto":
        return selected
    lowered = target.lower()
    if "windows" in lowered:
        return "pdb"
    if "wasm" in lowered or "web" in lowered:
        return "sourcemap"
    return "dwarf"


def _check_debug(component: ComponentResult, requested: str | None) -> ComponentResult:
    if requested is None:
        return component
    supported = component.capabilities.debug_formats
    aliases = {"pdb": {"pdb", "codeview"}, "codeview": {"pdb", "codeview"}}
    accepted = aliases.get(requested, {requested})
    if "*" in supported or accepted.intersection(supported):
        return component
    errors = (
        *component.errors,
        f"{component.kind} {component.name!r} does not support debug format "
        f"{requested!r}; supports {', '.join(supported) or 'none'}",
    )
    return replace(component, errors=errors)


def negotiate_build(argv: list[str]) -> NegotiationResult:
    """Negotiate effective backend/linker, including delegated debug builds."""

    backend_name = _option_value(argv, "--backend") or "legacy"
    backend = resolve_backend(backend_name)
    target = _option_value(argv, "--target") or "host"
    output_type = _option_value(argv, "--type") or "executable"
    sanitizers = active_sanitizers()
    speedy_lossy = speedy_lossy_enabled()
    debug_format = _resolved_debug_format(target)

    backend_result = negotiate_component(
        "backend",
        backend_name,
        backend,
        target=target,
        output_type=output_type,
        sanitizers=sanitizers,
        speedy_lossy=speedy_lossy,
    )
    backend_result = _check_debug(backend_result, debug_format)

    linker_name = _option_value(argv, "--linker")
    if linker_name is None and backend is not None:
        linker_name = getattr(backend, "default_linker", None)

    # The built-in PE/ELF linker deliberately delegates sanitizer and debugger
    # builds to GCC. Negotiate the component that will actually perform the link.
    if linker_name == "builtin" and (sanitizers or debug_format is not None):
        linker_name = "gcc"

    linker_result = None
    if linker_name:
        linker_result = negotiate_component(
            "linker",
            linker_name,
            resolve_linker(linker_name),
            target=target,
            output_type=output_type,
            sanitizers=sanitizers,
            speedy_lossy=speedy_lossy,
        )
        linker_result = _check_debug(linker_result, debug_format)

    return NegotiationResult(
        backend=backend_result,
        linker=linker_result,
        target=target,
        output_type=output_type,
        sanitizers=sanitizers,
        speedy_lossy=speedy_lossy,
    )


# Keep every existing importer—builds, graph plans, lockfiles, and extensions—on
# one source of truth without forcing public module renames.
from . import capability_negotiation as _base
from . import build_options as _build_options

_base.negotiate_build = negotiate_build
_original_inject = _build_options.inject_build_options


def _inject_extended_build_options(args):
    resolved = _original_inject(args)
    resolved["target_triple"] = os.environ.get("ASMPYTHON_TARGET_TRIPLE")
    resolved["fastcomp_state_path"] = os.environ.get("ASMPYTHON_FAST_STATE")
    return resolved


_build_options.inject_build_options = _inject_extended_build_options


__all__ = ["negotiate_build"]
