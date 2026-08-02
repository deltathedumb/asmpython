"""Scaffold for the planned VHDL backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["vhdl"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
