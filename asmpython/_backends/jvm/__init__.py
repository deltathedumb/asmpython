"""Scaffold for the planned JVM/JAR backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["jvm"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
