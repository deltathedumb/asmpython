"""Linking: backend artifacts to a program you can run.

    from apc.link import Toolchain, register

    class MyToolchain(Toolchain):
        name = "bare-metal"
        def link(self, request): ...

    register(MyToolchain())

Then `apc build prog.py --toolchain bare-metal`. See `docs/LINKERS.md`.
"""
from __future__ import annotations

from .base import LinkError, LinkRequest, Toolchain, find_tool, run
from .registry import available, get, load_builtin, register
from .runtime import ENTRY_SYMBOL, RUNTIME_C, needs_runtime, write_runtime

__all__ = [
    "ENTRY_SYMBOL", "LinkError", "LinkRequest", "RUNTIME_C", "Toolchain",
    "available", "find_tool", "get", "load_builtin", "needs_runtime",
    "register", "run", "write_runtime",
]
