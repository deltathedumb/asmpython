"""
asmpython's built-in x86-64 backend (driver.py's --backend x86-64).

Compiles an IRModule (see asmpython._compiler.ir / ir_lower.py) to a
relocatable object file (.o / .obj) using all available CPU cores, then
links it via whichever linker plugin is selected (asmpython/_linkers) --
`builtin` (this backend's own default: no gcc/ld involved at all) unless
overridden by driver.py's --linker flag.

Plugin interface (the convention asmpython._compiler.ir.ModuleBackend
adapts to IRBackend):
  requested_args: list[dict]
  default_linker: str
  run_backend_codegen(ir, args)       -> dict[str, bytes]
  run_backend_link(objects, args)     -> dict[str, bytes]
  __module_backend__: ModuleBackend   (so the driver can just import this
                                        module and use it directly)
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from typing import Any

from .regalloc import allocate
from .codegen import compile_func, FuncCode
from .elf import build_elf
from .coff import build_coff
from ..._linkers import builtin as _builtin_linker
from ..._linkers import gcc as _gcc_linker
from .phi_elim import eliminate_phi
from .unpack_normalize import install as _install_literal_unpack_normalizer
from ..._compiler.ir import ModuleBackend


# driver.py imports ir_lower immediately before this package and only calls
# lower_module after resolving __module_backend__. Install the backend-local,
# idempotent prepass at that point so sema-approved literal destructuring reaches
# the existing typed parallel-assignment lowering.
_install_literal_unpack_normalizer()


# ── CLI arguments this backend registers ─────────────────────────────────────

requested_args: list[dict] = [
    {
        "name":    "--target-os",
        "help":    "Object file format — linux, windows, or auto (detects host)",
        "default": "auto",
        "type":    str,
    },
    {
        "name":    "--abi",
        "help":    "Calling convention — sysv, win64, or auto",
        "default": "auto",
        "type":    str,
    },
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve(target_os: str, abi: str) -> tuple[str, str]:
    if target_os == "auto":
        target_os = "windows" if sys.platform == "win32" else "linux"
    else:
        target_os = target_os.lower()
    if abi == "auto":
        abi = "win64" if target_os == "windows" else "sysv"
    else:
        abi = abi.lower()
    return target_os, abi


# Top-level so ProcessPoolExecutor can pickle it
def _compile_one(payload: tuple) -> FuncCode:
    func, abi = payload
    alloc = allocate(func, abi)
    return compile_func(func, alloc, abi)


def _compile_all(funcs: list, abi: str) -> list[FuncCode]:
    """Compile every function, using as many cores as available."""
    n        = len(funcs)
    workers  = min(n, os.cpu_count() or 1)
    payloads = [(f, abi) for f in funcs]

    if workers <= 1:
        return [_compile_one(p) for p in payloads]

    # Try multi-process (true parallelism — each process has its own GIL).
    # Fall back to threads if spawn fails (e.g. frozen executable, REPL).
    name_order = [f.name for f in funcs]
    try:
        results: dict[str, FuncCode] = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_compile_one, p): p[0].name for p in payloads}
            for fut in as_completed(futs):
                fc = fut.result()           # re-raises any compilation error
                results[fc.name] = fc
        return [results[n] for n in name_order]

    except Exception:
        # Graceful fallback: threads still help with I/O overlap on larger IRs
        results = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_compile_one, p): p[0].name for p in payloads}
            for fut in as_completed(futs):
                fc = fut.result()
                results[fc.name] = fc
        return [results[n] for n in name_order]


# ── Plugin entry point ────────────────────────────────────────────────────────

def run_backend_codegen(ir: Any, args: dict) -> dict[str, bytes]:
    """
    Compile IRModule → relocatable object file.

    ir:   IRModule (duck-typed — accessed via .funcs and .data)
    args: dict of resolved CLI args (target_os, abi)

    Returns: {"output.o": <bytes>}  or  {"output.obj": <bytes>}
    """
    target_os, abi = _resolve(
        args.get("target_os", "auto"),
        args.get("abi",       "auto"),
    )

    if not ir.funcs:
        ext = ".obj" if target_os == "windows" else ".o"
        return {f"output{ext}": b""}

    # Phi elimination must run before register allocation (mutates functions in place).
    #
    # NOTE: `ir.layout_blocks_rpo` belongs here in principle -- reverse postorder
    # is what makes the allocator's "definitions precede uses" assumption true --
    # but it CANNOT land alone. See that function's docstring.
    for func in ir.funcs:
        eliminate_phi(func)

    func_codes = _compile_all(ir.funcs, abi)
    globals    = getattr(ir, "data", None) or []

    if target_os == "windows":
        return {"output.obj": build_coff(func_codes, globals)}
    else:
        return {"output.o": build_elf(func_codes, globals)}


# ── Linking ───────────────────────────────────────────────────────────────────

# This backend's own linker, used whenever args doesn't request a
# different one explicitly (driver.py's --linker flag). Builtin first --
# this backend's whole point is skipping external tools where it can.
default_linker = "builtin"

_LINKER_NAMES = ["gcc", "builtin"]


def run_backend_link(objects: list[bytes], args: dict) -> dict[str, bytes]:
    """
    Link object files (as produced by run_backend_codegen, plus whatever
    runtime-support objects the driver adds) into an executable.

    objects: list of raw object-file bytes (this backend's own output
             first, then any extra objects the driver appended -- order
             doesn't matter to the linker, only to symbol resolution,
             which doesn't care either since every linker resolves the
             full symbol set before applying relocations).
    args:    resolved CLI args, plus whatever driver.py adds as link
             context (target_os, entry_symbol, gcc_path, extra_args, ...).
             args["linker"] selects the linker by name; falls back to
             this module's own default_linker when absent/None.
    """
    target_os, _abi = _resolve(args.get("target_os", "auto"), args.get("abi", "auto"))
    linker_name = args.get("linker") or default_linker
    ctx = {**args, "objects": objects, "target_os": target_os}
    # The two built-in linkers stay a static if/elif rather than living in
    # the dict registry themselves: asmpython has no first-class module
    # values today, so a dict holding *these* two module references
    # wouldn't be representable under self-hosted compilation (see
    # RESUME.md's "First-class module values" pending item). The
    # third-party registry below (asmpython._linkers.get_linker) is a
    # plain Python dict too, but it's only ever consulted under
    # CPython-hosted compilation -- a third-party linker registered via
    # `asmpython.linker.Linker(...)` has no self-host story yet either way, so
    # this doesn't make anything self-host-unsafe that wasn't already.
    if linker_name == "gcc":
        out_bytes = _gcc_linker.link(ctx)
    elif linker_name == "builtin":
        out_bytes = _builtin_linker.link(ctx)
    else:
        from ..._linkers import get_linker, registered_names

        third_party = get_linker(linker_name)
        if third_party is None:
            have = ", ".join(_LINKER_NAMES + registered_names())
            raise ValueError(f"unknown linker {linker_name!r} (have: {have})")
        out_bytes = third_party.link(ctx)
    ext = ".exe" if target_os == "windows" else ""
    return {f"output{ext}": out_bytes}


__module_backend__ = ModuleBackend(sys.modules[__name__])
