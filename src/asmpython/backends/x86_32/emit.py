"""`x86-32` -- 32-bit x86, as a real object file.

NOT WRITTEN YET. This module exists so that 'x86-32' is a REGISTERED backend
that refuses, rather than a name `asmpython backends` has never heard of.

PLANNED TO SHARE THE x86-64 ENCODER rather than have one of its own. The
instruction encoding is the same problem in a narrower default operand
size, and two encoders for one instruction set would disagree eventually.
That sharing has to be designed INTO the x86-64 encoder while it is being
written; retrofitting it afterwards is most of the work twice.

`ready = False`, so the driver warns before it ever reaches `emit`, and `emit`
refuses with the work that is missing rather than with a traceback.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module


class X86_32Backend(Backend):
    name = "x86-32"
    description = "32-bit x86 machine code (ELF32/COFF)"
    kind = "binary"
    #: The whole point of this module. See the docstring.
    ready = False
    #: A PLACEHOLDER. The real one is 'i386-linux', which is registered when
    #: this backend can emit for it -- a target naming a platform nothing can
    #: compile for is the failure `x86_64-macos` already demonstrated.
    default_target = "c"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the x86-32 backend is not written yet. What it needs: "
            "the x86-64 encoder, in 32-bit mode, plus the ELF32 and COFF writers")


register(X86_32Backend())
