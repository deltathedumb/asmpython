"""Scaffold for the planned AMDGPU backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["amdgpu"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
