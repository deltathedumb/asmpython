"""Linking: backend artifacts to a program you can run.

    from asmpython.link import Toolchain, register

    class MyToolchain(Toolchain):
        name = "bare-metal"
        def link(self, request): ...

    register(MyToolchain())

Then `asmpython build prog.py --toolchain bare-metal`. See `docs/LINKERS.md`.

THE OBJECT RUNTIME IS NOT HERE. It was, and it was most of this package by
line count, which made `from asmpython.link import ...` the way a frontend
asked what `apy_add` takes. It lives in `asmpython.objects` now, and what is
left here is what the first line of this docstring claims.
"""
from __future__ import annotations

from .base import LinkError, LinkRequest, Toolchain, find_tool, run
from .baremetal import BareMetalToolchain, write_runtime_sources
from .registry import unregister, available, get, load_builtin, register

__all__ = [
    "BareMetalToolchain", "LinkError", "LinkRequest", "Toolchain",
    "available", "find_tool", "get", "load_builtin",
    "register", "unregister", "run", "write_runtime_sources",
]
