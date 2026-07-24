"""
asmpython's built-in x86 (32-bit / IA-32) backend (driver.py's --backend x86).

Compiles an IRModule (see asmpython._compiler.ir / ir_lower.py) to a
relocatable ELF32/COFF object file, then links it with this package's own
from-scratch ELF32/PE32 linkers (elf_linker.py/pe_linker.py) -- no gcc/ld
involved, mirroring x86_64's "builtin" default linker path. There is no
external "gcc" linker option here (unlike x86_64): a 32-bit cross-gcc/ld
toolchain isn't assumed to be on PATH, and the builtin linkers are already
real-toolchain-verified (see elf.py/elf_linker.py/coff.py/pe_linker.py's own
docstrings), so builtin is the only linker this backend registers.

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

import sys
from typing import Any

from .codegen import compile_func, FuncCode, _scan_needs_pic
from .regalloc import allocate
from .elf import build_elf
from .elf_linker import link_elf
from .coff import build_coff
from .pe_linker import link_pe
from .. import register_backend
from ..._compiler.ir import ModuleBackend


# ── CLI arguments this backend registers ─────────────────────────────────────

requested_args: list[dict] = [
    {
        "name":    "--target-os",
        "help":    "Object file format — linux, windows, or auto (detects host)",
        "default": "auto",
        "type":    str,
    },
]

default_linker = "builtin"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_target_os(target_os: str) -> str:
    if target_os == "auto":
        return "windows" if sys.platform == "win32" else "linux"
    return target_os.lower()


# ── Plugin entry point ────────────────────────────────────────────────────────

def run_backend_codegen(ir: Any, args: dict) -> dict[str, bytes]:
    """
    Compile IRModule -> relocatable object file.

    ir:   IRModule (duck-typed -- accessed via .funcs and .data)
    args: dict of resolved CLI args (target_os)

    Returns: {"output.o": <bytes>}  or  {"output.obj": <bytes>}
    """
    target_os = _resolve_target_os(args.get("target_os", "auto"))
    ext = ".obj" if target_os == "windows" else ".o"

    if not ir.funcs:
        return {f"output{ext}": b""}

    func_codes: list[FuncCode] = []
    for func in ir.funcs:
        # cdecl is x86-32's only calling convention here (no SysV/win64
        # split the way x86-64 has -- both this target's OSes use cdecl
        # for a plain C-ABI function; the real Windows/Linux ELF vs. COFF
        # divergence lives entirely in the object-file writer, not the
        # register-allocation/codegen ABI). needs_pic is a per-function
        # property of the IR (does this function reference a global?),
        # not a target-wide toggle -- codegen.py's own _scan_needs_pic
        # computes it directly from the function body, and both
        # allocate() and compile_func() need the same value (regalloc
        # must exclude EBX from its allocatable pool when PIC needs it
        # as the GOT-pointer register; codegen must know to route global
        # references through that same GOT pointer).
        needs_pic = _scan_needs_pic(func)
        alloc = allocate(func, "cdecl", needs_pic)
        func_codes.append(compile_func(func, alloc, needs_pic))

    globals_ = getattr(ir, "data", None) or []

    if target_os == "windows":
        return {"output.obj": build_coff(func_codes, globals_)}
    else:
        return {"output.o": build_elf(func_codes, globals_)}


# ── Linking ───────────────────────────────────────────────────────────────────

def run_backend_link(objects: list[bytes], args: dict) -> dict[str, bytes]:
    """
    Link object files (as produced by run_backend_codegen) into an
    executable or shared library, using this backend's own from-scratch
    ELF32/PE32 linker -- there is no external-linker option to fall back
    to here (see this module's own docstring).

    objects: list of raw object-file bytes.
    args:    resolved CLI args, plus whatever driver.py adds as link
             context (target_os, entry_symbol, output_type, exports,
             soname, ...).
    """
    target_os = _resolve_target_os(args.get("target_os", "auto"))
    entry_symbol = args.get("entry_symbol", "main")
    is_library = args.get("output_type") == "library"
    exports = args.get("exports") or []

    if target_os == "windows":
        out_bytes = link_pe(
            objects,
            entry_symbol,
            is_library=is_library,
            exports=exports,
        )
        ext = "" if is_library else ".exe"
        name = "output.dll" if is_library else f"output{ext}"
    else:
        soname = args.get("soname") or "libportapy.so"
        out_bytes = link_elf(
            objects,
            entry_symbol,
            is_library=is_library,
            exports=exports,
            soname=soname,
        )
        name = "output.so" if is_library else "output"

    return {name: out_bytes}


__module_backend__ = ModuleBackend(sys.modules[__name__])

# Replace the "x86" scaffold registered by register_scaffold_backends with
# this real implementation -- _backends/__init__.py's own docstring
# explicitly sanctions this ("Registering a real backend under the same
# canonical name deliberately replaces its scaffold"). Runs at import time;
# _backends/__init__.py imports this module eagerly (right after
# registering every scaffold) specifically so this replacement happens
# before any --backend x86 lookup, since nothing else imports this
# subpackage on its own (unlike x86_64, which driver.py special-cases and
# imports directly, bypassing the registry entirely).
register_backend("x86", __module_backend__)
