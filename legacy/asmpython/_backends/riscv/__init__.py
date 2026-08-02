"""Scaffold for the planned RISC-V backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["riscv"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
