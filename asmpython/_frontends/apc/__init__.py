"""The APC frontend: ASMPython's low-level systems language (``.apc``).

APC ("ASMPython C") targets the neutral SSA IR **directly** rather than
producing the typed Python AST ``ir_lower`` consumes -- the second of the two
return shapes ``_compiler/ir_contract.md`` documents for a frontend. Selected
with ``--frontend apc``.

Implemented today (the low-level core, which maps 1:1 onto the IR):

* ``func`` / ``extern func``, C-ABI calls, trailing return types
* fixed-width scalars (``i8``..``u64``, ``f32``/``f64``, ``ptr``, ``bool``)
* ``const`` (immutable) / ``let`` (mutable), with direct-SSA emission where
  a binding is assigned once and never address-taken
* ``if`` / ``else`` / ``while`` / ``for (i = a..b)`` / ``break`` / ``continue``
* ``layout`` -- explicit-offset foreign memory, packed by default, interpreted
  at the use site with ``as``
* ``enum`` -- symbolic, and ``enum X[u8]`` with pinned values
* ``sizeof``, casts, string literals (interned into ``.rodata``), ``export``

Not yet lowered: ``type`` classes, ``string`` values, generics. These parse,
then fail with a source-located message rather than being half-emitted.
"""

from __future__ import annotations

from ..._compiler.ir import FrontendContext, IRFrontend, IRModule
from .emit import emit_module
from .errors import APCError
from .parser import parse


class APCFrontend(IRFrontend):
    """Parse APC source and emit an ``IRModule``."""

    name = "apc"
    source_extensions = (".apc",)
    production_suitable = False

    def parse(self, src: str, ctx: FrontendContext) -> IRModule:
        return emit_module(parse(src), src)


__all__ = ["APCError", "APCFrontend", "emit_module", "parse"]
