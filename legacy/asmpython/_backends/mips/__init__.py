"""Scaffold for the planned MIPS backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["mips"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
