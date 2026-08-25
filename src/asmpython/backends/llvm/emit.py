"""`llvm` -- LLVM IR, as text.

NOT WRITTEN YET. This module exists so that 'llvm' is a REGISTERED backend
that refuses, rather than a name `asmpython backends` has never heard of --
and so that `llvm.alib` has an owner. A backend is the thing that can emit an
architecture's instructions, so it is the thing that declares them; see
`backend/alib.py`.

A LANGUAGE BACKEND, so text is the artifact and no encoder is needed --
the same bargain the C backend takes. It is the cheapest of the six by
a wide margin and reaches every platform LLVM does, which makes it the
sensible one to write first.

LLVM ALREADY NAMES EVERY ALIB INTRINSIC (`llvm.ctpop.i64`,
`llvm.readcyclecounter`, `fence`, `volatile`), so `llvm.alib` is the
one that can be lowered without inventing anything.

`ready = False`, so the driver warns before it ever reaches `emit`, and `emit`
refuses with the work that is missing rather than with a traceback.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module
from .alib import ALIB


class LlvmBackend(Backend):
    name = "llvm"
    description = "LLVM IR (.ll) for any target LLVM supports"
    kind = "language"
    #: The whole point of this module. See the docstring.
    ready = False
    #: A PLACEHOLDER. The real one is 'c', which is registered when
    #: this backend can emit for it -- a target naming a platform nothing can
    #: compile for is the failure `x86_64-macos` already demonstrated.
    default_target = "c"
    alib = ALIB

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the llvm backend is not written yet. What it needs: "
            "an SSA-form printer; the IR's mutable registers need mem2reg or an alloca per register")


register(LlvmBackend())
