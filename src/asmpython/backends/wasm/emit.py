"""`wasm` -- WebAssembly, as a binary module.

NOT WRITTEN YET. This module exists so that 'wasm' is a REGISTERED backend
that refuses, rather than a name `asmpython backends` has never heard of.

A BINARY BACKEND, NOT A `.wat` ONE. The text format would be text, which
`Backend.kind` forbids for anything that is not another language -- and
the binary format is barely harder: sections, LEB128 integers, and a
stack machine that the IR's expression trees map onto directly.

THE BEST FIT OF THE THREE non-machine targets: wasm's linear memory is a
byte array at an address, which is exactly what the IR's memory model
already is. Nothing has to be emulated.

`ready = False`, so the driver warns before it ever reaches `emit`, and `emit`
refuses with the work that is missing rather than with a traceback.
"""
from __future__ import annotations

from ...backend.base import Backend, BackendUnsupported, Target, register
from ...ir import Module


class WasmBackend(Backend):
    name = "wasm"
    description = "WebAssembly binary modules (.wasm) for WASI and the browser"
    kind = "binary"
    #: The whole point of this module. See the docstring.
    ready = False
    #: A PLACEHOLDER. The real one is 'wasm32-wasi', which is registered when
    #: this backend can emit for it -- a target naming a platform nothing can
    #: compile for is the failure `x86_64-macos` already demonstrated.
    default_target = "c"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        raise BackendUnsupported(
            "the wasm backend is not written yet. What it needs: "
            "a wasm module writer (sections, LEB128) and a stack-machine lowering")


register(WasmBackend())
