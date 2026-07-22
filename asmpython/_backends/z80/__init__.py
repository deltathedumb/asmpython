"""Scaffold for the planned Z80-family backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["z80"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
