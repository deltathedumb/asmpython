"""Scaffold for the planned BEAM backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["beam"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
