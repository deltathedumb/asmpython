"""builtin linker: asmpython's own from-scratch linker, no gcc/ld/link.exe.

Dispatches by target_os to a format-specific implementation:
  windows -> asmpython._backends.x86_64.pe_linker  (PE32+, import tables)
  linux   -> asmpython._backends.x86_64.elf_linker (ET_EXEC, GOT + copy relocs)
  macos   -> not implemented yet (planned: Mach-O, lazy binding)

Each implementation takes the same merge-objects/resolve-symbols/patch-
relocations approach; only the container format and import mechanism
differ. See pe_linker.py / elf_linker.py's module docstrings for the
mechanism in detail.
"""

from __future__ import annotations

requested_args: list[dict] = []


def link(ctx: dict) -> bytes:
    target_os = ctx.get("target_os", "windows")
    objects: list[bytes] = ctx["objects"]
    entry_symbol = ctx.get("entry_symbol", "main")

    if target_os == "windows":
        from asmpython._backends.x86_64.pe_linker import link_pe

        return link_pe(objects, entry_symbol=entry_symbol)

    if target_os == "linux":
        from asmpython._backends.x86_64.elf_linker import link_elf

        return link_elf(objects, entry_symbol=entry_symbol)

    raise NotImplementedError(
        f"--linker builtin doesn't support target_os={target_os!r} yet "
        "(windows/linux only for now -- macos is planned, see pe_linker.py)"
    )
