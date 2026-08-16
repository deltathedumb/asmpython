"""Scaffold for the planned GLSL backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["glsl"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
