"""Scaffold for the planned CPython bytecode backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["python-bytecode"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
