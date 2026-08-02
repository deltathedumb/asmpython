"""Scaffold for the planned MOS 6502-family backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["6502"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
