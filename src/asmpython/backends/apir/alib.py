"""`apir.alib` -- and why it declares nothing.

NOT A MISSING FILE, and no longer a placeholder for an unidentified format.
APIR is ASMPython's own Intermediate Representation, serialised into a
versioned container -- see `emit.py` beside this one. So the question an alib
answers, "what can this MACHINE do that a portable language cannot reach",
has no subject here: APIR is not a machine. It has no address space to map a
device into, no privileged mode, no instruction stream, and no cycle counter,
because it is a description of a program rather than a thing that runs one.

WHICH MACHINE'S ALIB APPLIES IS DECIDED LATER, by whatever compiles the
container. A program that reaches for `x86_64.alib` and is frozen to `.apir`
still names x86-64 in its own source; the intrinsics travel in the IR as the
calls they already are, and the backend that finally emits code is the one
that lowers them.

So this stays empty for a reason that will not change, unlike `jvm.alib` and
`pybc.alib` -- which are also small, but small because their machines are
managed rather than because they are not machines at all.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="apir",
    doc="APIR/OpenCIR: a placeholder; the format has not been identified.",
    groups={
    },
)
