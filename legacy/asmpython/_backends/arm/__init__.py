"""Scaffold for the planned broad ARM backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["arm"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
