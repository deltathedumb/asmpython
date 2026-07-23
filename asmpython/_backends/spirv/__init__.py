"""Scaffold for the planned SPIR-V backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["spirv"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
