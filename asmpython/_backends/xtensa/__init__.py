"""Scaffold for the planned Xtensa backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["xtensa"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
