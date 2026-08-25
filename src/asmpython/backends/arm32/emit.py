"""`arm32` -- 32-bit ARM, and the Raspberry Pi.

NOT WRITTEN YET. This module exists so that 'arm32' is a REGISTERED backend
that refuses, rather than a name `asmpython backends` has never heard of --
and so that `arm32.alib` has an owner. A backend is the thing that can emit an
architecture's instructions, so it is the thing that declares them; see
`backend/alib.py`.

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
from .alib import ALIB


class Arm32Backend(Backend):
    name = "arm32"
    description = "ARMv7-A machine code (ELF32)"
    kind = "binary"
    #: The whole point of this module. See the docstring.
    ready = False
    #: A PLACEHOLDER. The real one is 'armv7-linux', which is registered when
    #: this backend can emit for it -- a target naming a platform nothing can
    #: compile for is the failure `x86_64-macos` already demonstrated.
    default_target = "c"
    alib = ALIB

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the arm32 backend is not written yet. What it needs: "
            "an ARMv7 encoder and the ELF32 writer")


register(Arm32Backend())
