"""What a backend needs to know about the machine it emits for.

A *target* is a platform: an architecture, an operating system, a calling
convention, an object format. A *backend* is a code generator. They are
separate because they vary independently -- one code generator serves several
platforms, and one platform can be reached by several code generators -- and
because a compiler that hard-codes a list of platforms grows a new line every
time someone wants a new one.

EVERY FIELD IS EXPLICIT, AND THAT IS THE POINT. An earlier version of this
chose the calling convention with `"windows" in target.name`. It worked for
the two targets that shipped and silently produced System V code for anything
else -- a target named `win64-custom` would have compiled, linked, and passed
its arguments in the wrong registers. Sniffing a name is a guess; a field is
an answer, and a third-party target has no way to declare a guess.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Target:
    """One platform.

    A backend that genuinely supports only one shape simply ignores the fields
    it does not vary over; nothing here is mandatory to consult.
    """

    #: Canonical name, conventionally `<arch>-<os>`. Identity only -- never
    #: parse it to decide behaviour; read the field that says what you mean.
    name: str
    arch: str = "x86_64"
    os: str = "linux"
    #: Calling convention: "sysv", "win64", or a backend-defined name. The one
    #: field a machine backend must not get wrong.
    abi: str = "sysv"
    #: What the assembler stage should produce: "elf", "coff", "macho", or
    #: "source" for a backend that emits another language.
    object_format: str = "elf"
    pointer_size: int = 8
    little_endian: bool = True
    #: Stack alignment required at a call boundary, in bytes.
    stack_alignment: int = 16
    #: Filename suffixes for the link stage.
    object_suffix: str = ".o"
    executable_suffix: str = ""
    #: Compiler drivers that can assemble and link for this target, in
    #: preference order. Empty means the host's own -- a cross target names
    #: its toolchain here so the link stage does not have to guess from the
    #: architecture, and so a second toolchain for the same architecture is a
    #: registration rather than a special case.
    cc_names: tuple[str, ...] = ()
    #: Which registered toolchain turns this target's artifacts into a program,
    #: when the user names none. Empty means the C compiler driver.
    #:
    #: A field for the same reason `abi` is one: a target whose artifacts are
    #: class files cannot be linked by `cc`, and the alternative to saying so
    #: is the driver recognising platforms by name.
    default_toolchain: str = ""

    @property
    def pointer_bits(self) -> int:
        return self.pointer_size * 8

    @property
    def is_source(self) -> bool:
        """True if this target's artifacts are source, not objects."""
        return self.object_format == "source"

    def __str__(self) -> str:
        return self.name
