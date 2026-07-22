"""ASMPython's from-scratch linker for ordinary native builds.

Sanitizer runtimes and native debugger section/PDB handling are external
-toolchain concerns today. Requested sanitizer or debug builds deliberately
delegate to GCC rather than silently dropping the requested metadata.
"""
from __future__ import annotations

requested_args: list[dict] = []
production_suitable = True


def link(ctx: dict) -> bytes:
    if ctx.get("sanitizers") or ctx.get("debug"):
        from . import gcc
        return gcc.link(ctx)

    target_os = ctx.get("target_os", "windows")
    objects: list[bytes] = ctx["objects"]
    entry_symbol = ctx.get("entry_symbol", "main")

    if target_os == "windows":
        from asmpython._backends.x86_64.pe_linker import link_pe
        return link_pe(objects, entry_symbol=entry_symbol)

    if target_os == "linux":
        from asmpython._backends.x86_64 import elf_linker
        elf_linker._SO_FOR_SYMBOL.setdefault("dlopen", "libdl.so.2")
        elf_linker._SO_FOR_SYMBOL.setdefault("dlsym", "libdl.so.2")
        return elf_linker.link_elf(objects, entry_symbol=entry_symbol)

    raise NotImplementedError(
        f"--linker builtin doesn't support target_os={target_os!r} yet "
        "(windows/linux only for now -- macos is planned)"
    )
