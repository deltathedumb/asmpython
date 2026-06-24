"""
asmpython's built-in x86-64 backend (driver.py's --backend x86-64).

Compiles an IRModule (see asmpython._compiler.ir / ir_lower.py) to a
relocatable object file (.o / .obj) using all available CPU cores. The
result is linkable with gcc, clang, or MSVC link.

Plugin interface:
  run_backend_codegen(ir, args) -> dict[str, bytes]
  requested_args: list[dict]
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
from .phi_elim import eliminate_phi


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
    for func in ir.funcs:
        eliminate_phi(func)

    func_codes = _compile_all(ir.funcs, abi)
    globals    = getattr(ir, "data", None) or []

    if target_os == "windows":
        return {"output.obj": build_coff(func_codes, globals)}
    else:
        return {"output.o": build_elf(func_codes, globals)}
