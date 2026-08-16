"""Scaffold for the planned eBPF backend."""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["ebpf"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
