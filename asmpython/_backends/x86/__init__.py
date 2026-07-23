"""Scaffold for the planned broad x86 backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["x86"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
