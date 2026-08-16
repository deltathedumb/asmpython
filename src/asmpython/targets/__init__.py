"""The targets that ship with asmpython.

Every one is an ordinary `register()` call -- there is no privileged path for
built-ins. If this file were deleted, asmpython would still compile; it would simply
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

#: Bare-metal AArch64: no operating system, no libc entry point. This is the
#: one that can be EXECUTED on a developer machine without an ARM box --
#: qemu-system-aarch64 boots it directly with `-M virt -kernel`, which is why
#: the arm64 backend defaults to it and the tests use it.
AARCH64_NONE = register(Target(
    "aarch64-none", arch="aarch64", os="none", abi="aapcs64",
    object_format="elf", object_suffix=".o", executable_suffix=".elf",
    cc_names=("aarch64-none-elf-gcc", "aarch64-elf-gcc"),
), aliases=("arm64-bare", "aarch64-elf"))

AARCH64_LINUX = register(Target(
    "aarch64-linux", arch="aarch64", os="linux", abi="aapcs64",
    object_format="elf", object_suffix=".o", executable_suffix="",
    cc_names=("aarch64-linux-gnu-gcc", "aarch64-none-linux-gnu-gcc"),
), aliases=("arm64", "arm64-linux"))

AARCH64_MACOS = register(Target(
    "aarch64-macos", arch="aarch64", os="macos", abi="aapcs64",
    object_format="macho", object_suffix=".o", executable_suffix="",
    cc_names=("clang",),
), aliases=("arm64-macos", "apple-silicon"))

#: The Java virtual machine. Not a machine anyone builds: the architecture is
#: the bytecode, the "object format" is a class file, and there is no calling
#: convention to get wrong because the JVM's own is the only one.
#:
#: `pointer_size` is 8 and the byte order little-endian because the IR's memory
#: is a byte array this target's backend indexes itself -- those two fields
#: describe that array, not a JVM, which has no addressable memory at all.
JVM = register(Target(
    "jvm", arch="jvm", os="jvm", abi="jvm",
    object_format="class", object_suffix=".class", executable_suffix=".jar",
    stack_alignment=8, default_toolchain="jar",
), aliases=("java", "jar"))

__all__ = ["PORTABLE_C", "X86_64_LINUX", "X86_64_WINDOWS", "X86_64_MACOS",
           "AARCH64_NONE", "AARCH64_LINUX", "AARCH64_MACOS", "JVM"]
