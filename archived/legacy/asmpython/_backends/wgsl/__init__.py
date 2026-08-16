"""Scaffold for the planned WGSL backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["wgsl"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
