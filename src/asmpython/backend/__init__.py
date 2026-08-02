"""Backend interface, target description, and shared code-generation services.

`liveness` and `regalloc` are LIBRARIES, not stages. A backend may ignore them
entirely and give every virtual register its own stack slot -- `backends/c`
does exactly that. They exist so the backends that want speed do not each write
their own, which is how the tree this replaces ended up with two copies of one
analysis that silently diverged.
"""
from .base import (
    ENTRY_SYMBOL, Backend, BackendUnsupported, Target, available, get,
    load_builtin, register,
)
from .liveness import LiveInterval, Liveness, compute_intervals
from .regalloc import (
    Allocation, InRegister, InSlot, RegisterFile, allocate, verify_allocation,
)

__all__ = [
    "Allocation", "Backend", "BackendUnsupported", "ENTRY_SYMBOL",
    "InRegister", "InSlot", "LiveInterval", "Liveness", "RegisterFile",
    "Target", "allocate", "available", "compute_intervals", "get",
    "load_builtin", "register", "verify_allocation",
]
