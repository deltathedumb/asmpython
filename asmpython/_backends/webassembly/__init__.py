"""Scaffold for the planned WebAssembly backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["webassembly"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
