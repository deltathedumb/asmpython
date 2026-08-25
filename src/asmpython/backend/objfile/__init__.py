"""Object-file writers: the half of a backend that `as` used to do.

A backend that emits assembly has not encoded anything -- it wrote text and
handed the real work to an assembler, which is why `Backend.kind` calls that
out and why these modules exist. One writer per format, shared by every
architecture that uses it, because the format does not vary between them:

    elf     Linux, and every bare-metal target
    coff    Windows                              (not written yet)
    macho   macOS                                (not written yet)

NOTHING HERE KNOWS AN INSTRUCTION. A writer takes bytes, symbols and
relocations; what those bytes mean is the architecture's business. That split
is what lets AArch64 and x86-64 share one ELF implementation.
"""
from __future__ import annotations

from .elf import (
    EM_386, EM_AARCH64, EM_ARM, EM_X86_64,
    SHF_ALLOC, SHF_EXECINSTR, SHF_WRITE,
    SHT_NOBITS, SHT_PROGBITS,
    STB_GLOBAL, STB_LOCAL, STT_FUNC, STT_OBJECT,
    ElfObject, Relocation, Section, Symbol,
)

__all__ = [
    "EM_386", "EM_AARCH64", "EM_ARM", "EM_X86_64", "ElfObject", "Relocation",
    "SHF_ALLOC", "SHF_EXECINSTR", "SHF_WRITE", "SHT_NOBITS", "SHT_PROGBITS",
    "STB_GLOBAL", "STB_LOCAL", "STT_FUNC", "STT_OBJECT", "Section", "Symbol",
]
