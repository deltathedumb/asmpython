"""asmlib — comprehensive hardware, network, and GUI library for asmpython.

Modules:
  hardware  — bare-metal port I/O, MMIO, timestamp counter (freestanding)
  network   — BSD socket API: socket/connect/send/recv/... (hosted)
  gui       — SDL2 window/renderer/event loop (hosted)

Each module exposes a BINDINGS dict of Func and Const entries, just like
stdlib modules, so the compiler's existing import-resolution path handles
them identically.  The registry ASMLIB_BINDINGS maps short names (as the
user writes them, e.g. `import hardware`) to the bindings dict.
"""
from __future__ import annotations

from ..stdlib import Func, Const  # reuse the same dataclasses

from .hardware import BINDINGS as _HW_BINDINGS
from .network import BINDINGS as _NET_BINDINGS
from .gui import BINDINGS as _GUI_BINDINGS

ASMLIB_BINDINGS: dict[str, dict] = {
    "hardware": _HW_BINDINGS,
    "network":  _NET_BINDINGS,
    "gui":      _GUI_BINDINGS,
}
