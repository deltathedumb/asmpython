"""Targets: the platforms asmpython can emit for.

    from asmpython.target import Target, register

    register(Target("riscv64-linux", arch="riscv64", os="linux",
                    abi="lp64d", object_format="elf"),
             aliases=("rv64",))

Then `asmpython build prog.py --target riscv64-linux`. See `docs/TARGETS.md`.
"""
from __future__ import annotations

from .base import Target
from .registry import (
    HOST, aliases, available, get, host, load_builtin, register, resolve,
)

__all__ = ["HOST", "Target", "register", "get", "available", "aliases",
           "resolve", "host", "load_builtin"]
