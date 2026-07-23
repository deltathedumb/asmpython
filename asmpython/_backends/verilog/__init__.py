"""Scaffold for the planned Verilog backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["verilog"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
