"""The targets that ship with apc.

Every one is an ordinary `register()` call -- there is no privileged path for
built-ins. If this file were deleted, apc would still compile; it would simply
have no platforms until something registered one, which is the property that
makes the extension point real rather than decorative.

`docs/TARGETS.md` explains adding your own.
"""
from __future__ import annotations

from ..target.base import Target
from ..target.registry import register

#: The reference target for the C backend. Not a machine: the "object format"
#: is source, and the fields describing a machine are the ones a C compiler
#: will decide for itself later.
PORTABLE_C = register(Target(
    "c", arch="any", os="any", abi="none", object_format="source",
    object_suffix=".c",
), aliases=("portable", "source"))

X86_64_LINUX = register(Target(
    "x86_64-linux", arch="x86_64", os="linux", abi="sysv",
    object_format="elf", object_suffix=".o", executable_suffix="",
), aliases=("linux", "x86_64-unknown-linux-gnu"))

X86_64_WINDOWS = register(Target(
    "x86_64-windows", arch="x86_64", os="windows", abi="win64",
    object_format="coff", object_suffix=".obj", executable_suffix=".exe",
), aliases=("windows", "win64", "x86_64-pc-windows-msvc"))

X86_64_MACOS = register(Target(
    "x86_64-macos", arch="x86_64", os="macos", abi="sysv",
    object_format="macho", object_suffix=".o", executable_suffix="",
), aliases=("macos", "darwin"))

__all__ = ["PORTABLE_C", "X86_64_LINUX", "X86_64_WINDOWS", "X86_64_MACOS"]
