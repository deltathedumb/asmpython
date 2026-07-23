"""Scaffold for the planned Intel 8051/MCS-51 backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["8051"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
