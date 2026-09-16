"""Object-file writers: the half of a backend that `as` used to do.

A backend that emits assembly has not encoded anything -- it wrote text and
handed the real work to an assembler, which is why `Backend.kind` calls that
out and why these modules exist. One writer per format, shared by every
architecture that uses it, because the format does not vary between them:

    elf     Linux, and every bare-metal target
    coff    Windows
    macho   macOS

NOTHING HERE KNOWS AN INSTRUCTION. A writer takes bytes, symbols and
relocations; what those bytes mean is the architecture's business. That split
is what lets AArch64 and x86-64 share one ELF implementation.

WHERE THE ADDEND LIVES is the difference that catches people, so it is worth
stating once: ELF keeps it in the relocation record, and COFF and Mach-O keep
it in the section data under the field being patched. Translating one to the
other needs to know whether a relocation is PC-relative and how wide its field
is -- both facts about the architecture -- so each BACKEND does that
translation, and these writers only record whatever addend they are handed.

THE ELF TYPES KEEP THE PLAIN NAMES (`Symbol`, `Relocation`, `Section`) for the
callers that predate the other two formats; COFF's and Mach-O's are exported
with a prefix. The three are deliberately the same SHAPE, so a backend can
build one list of symbols and hand it to whichever writer its target asks for.
"""
from __future__ import annotations

from .coff import (
    IMAGE_FILE_MACHINE_AMD64, IMAGE_FILE_MACHINE_ARM64,
    IMAGE_FILE_MACHINE_I386,
    IMAGE_SCN_CNT_CODE, IMAGE_SCN_CNT_INITIALIZED_DATA,
    IMAGE_SCN_CNT_UNINITIALIZED_DATA,
    IMAGE_SCN_MEM_EXECUTE, IMAGE_SCN_MEM_READ, IMAGE_SCN_MEM_WRITE,
    CoffObject,
)
from .coff import Relocation as CoffRelocation
from .coff import Symbol as CoffSymbol
from .elf import (
    EM_386, EM_AARCH64, EM_ARM, EM_X86_64,
    SHF_ALLOC, SHF_EXECINSTR, SHF_WRITE,
    SHT_NOBITS, SHT_PROGBITS,
    STB_GLOBAL, STB_LOCAL, STT_FUNC, STT_OBJECT,
    ElfObject, Relocation, Section, Symbol,
)
from .macho import (
    CPU_SUBTYPE_ARM64_ALL, CPU_SUBTYPE_X86_64_ALL, CPU_TYPE_ARM64,
    CPU_TYPE_X86_64, MachoObject,
)
from .macho import Relocation as MachoRelocation
from .macho import Symbol as MachoSymbol

__all__ = [
    "CoffObject", "CoffRelocation", "CoffSymbol",
    "MachoObject", "MachoRelocation", "MachoSymbol",
    "CPU_SUBTYPE_ARM64_ALL", "CPU_SUBTYPE_X86_64_ALL", "CPU_TYPE_ARM64",
    "CPU_TYPE_X86_64",
    "IMAGE_FILE_MACHINE_AMD64", "IMAGE_FILE_MACHINE_ARM64",
    "IMAGE_FILE_MACHINE_I386", "IMAGE_SCN_CNT_CODE",
    "IMAGE_SCN_CNT_INITIALIZED_DATA", "IMAGE_SCN_CNT_UNINITIALIZED_DATA",
    "IMAGE_SCN_MEM_EXECUTE", "IMAGE_SCN_MEM_READ", "IMAGE_SCN_MEM_WRITE",
    "EM_386", "EM_AARCH64", "EM_ARM", "EM_X86_64", "ElfObject", "Relocation",
    "SHF_ALLOC", "SHF_EXECINSTR", "SHF_WRITE", "SHT_NOBITS", "SHT_PROGBITS",
    "STB_GLOBAL", "STB_LOCAL", "STT_FUNC", "STT_OBJECT", "Section", "Symbol",
]
