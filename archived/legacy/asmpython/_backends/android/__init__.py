"""Scaffold for the planned Android (Dalvik/ART) backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["android"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
