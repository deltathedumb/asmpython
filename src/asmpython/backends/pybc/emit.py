"""`pybc` -- CPython bytecode, as a `.pyc`.

NOT WRITTEN YET. This module exists so that 'pybc' is a REGISTERED backend
that refuses, rather than a name `asmpython backends` has never heard of.

THE ONE THAT NEEDS THE IR TO SAY MORE THAN IT DOES. Every other backend
here targets something with an address space; CPython has objects and
names instead, so `ptr.load` of a computed address has no counterpart
short of emulating memory in a `bytearray`.

It does not have to come to that. The dynamic half of the Python
frontend already lowers to `ptr` values and `call @apy_*`, and every one
of those calls IS a Python operation -- `apy_add` is `+`. What is
missing is a way for the IR to SAY so, so a backend can recover the
operation instead of pattern-matching a symbol name. See `docs/PYC.md`.

`ready = False`, so the driver warns before it ever reaches `emit`, and `emit`
refuses with the work that is missing rather than with a traceback.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module


class PycBackend(Backend):
    name = "pybc"
    description = "CPython bytecode (.pyc) executable by the host interpreter"
    kind = "binary"
    #: The whole point of this module. See the docstring.
    ready = False
    #: A PLACEHOLDER. The real one is 'pybc', which is registered when
    #: this backend can emit for it -- a target naming a platform nothing can
    #: compile for is the failure `x86_64-macos` already demonstrated.
    default_target = "c"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the pybc backend is not written yet. What it needs: "
            "semantic tags on the object-runtime calls, a role on globals, and a code-object writer")


register(PycBackend())
