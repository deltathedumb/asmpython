"""`arm32` -- 32-bit ARM, and the Raspberry Pi.

NOT WRITTEN YET. This module exists so that 'arm32' is a REGISTERED backend
that refuses, rather than a name `uasm plugin backends` has never heard of.

THE FREESTANDING TARGET IS THE INTERESTING ONE. A Raspberry Pi is the
ARM32 machine most people can actually run bare metal, and it is
reachable under `qemu-system-arm` without any hardware at all -- which
is what makes this backend testable to the same standard as AArch64.

`ready = False`, so the driver warns before it ever reaches `emit`, and `emit`
refuses with the work that is missing rather than with a traceback.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module


class Arm32Backend(Backend):
    name = "arm32"
    #: An ELF32 object, or the assembly it came from.
    artifacts = (".o", ".s")
    description = "ARMv7-A machine code (ELF32)"
    kind = "binary"
    #: The whole point of this module. See the docstring.
    ready = False
    #: A PLACEHOLDER. The real one is 'armv7-linux', which is registered when
    #: this backend can emit for it -- a target naming a platform nothing can
    #: compile for is the failure `x86_64-macos` already demonstrated.
    default_target = "c"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the arm32 backend is not written yet. What it needs, in the "
            "order the work has to happen: a target-width `ptr` in the IR "
            "(`ir/types.py` fixes it at eight bytes) and an object runtime "
            "that reads it, which is around 670 sites across 45 runtime "
            "modules written with literal eight-byte offsets; then an ARMv7 "
            "encoder -- a different instruction set from AArch64, not a "
            "narrower one -- an AAPCS32 emitter, and the ELF32 writer")


register(Arm32Backend())
