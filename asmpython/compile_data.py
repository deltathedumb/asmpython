"""``COMPILE_DATA`` -- what the compiler knew, frozen into the program.

    import asmpython

    if asmpython.COMPILE_DATA.target == "windows":
        ...
    if asmpython.COMPILE_DATA.endian == "big":
        ...

Facts fixed when the program was built: which backend produced it, which
platform and ABI it targets, pointer width, byte order. They cannot change at
runtime, because they were decided before there was a runtime -- so the object
is immutable, and assigning to it is an error rather than a silently ignored
write.

This exists so a program can branch on its target without asking the host.
``sys.platform`` answers "what am I running on", which is a different and
usually wrong question for cross-compiled code: a Linux binary built on
Windows must take the Linux branch, and only the compiler knows that.

Under CPython -- running the compiler itself, or a test -- the fields describe
the host, since no build has fixed them. ``is_compiled`` distinguishes the two,
and is the only field whose answer depends on how you are executing.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, fields, replace
from typing import Any


@dataclass(frozen=True)
class CompileData:
    """Immutable description of the build that produced this program."""

    #: Backend that generated the code: "x86-64", "legacy", "jvm", "ternary",
    #: or a registered backend's name.
    backend: str = "cpython"

    #: Target OS: "windows", "linux", "freestanding", "freestanding16", "jvm".
    target: str = "cpython"

    #: Calling convention: "win64", "sysv", "aapcs64", or "" where the concept
    #: does not apply (a bytecode backend has no ABI in this sense).
    abi: str = ""

    #: Instruction set family: "x86_64", "arm64", "jvm", "ternary".
    arch: str = ""

    #: Pointer width in bits.
    pointer_bits: int = 64

    #: Byte order of the target: "little" or "big". Not the host's.
    endian: str = "little"

    #: Source-language frontend: "python", "apc", or a registered name.
    frontend: str = "python"

    #: Optimization passes actually run, in order.
    passes: tuple[str, ...] = ()

    #: Compiler version that produced this program.
    compiler_version: str = ""

    #: False when running under CPython rather than in a compiled program.
    #: The only field whose value depends on how you are executing rather than
    #: on how you were built.
    is_compiled: bool = False

    def __post_init__(self) -> None:
        if self.endian not in ("little", "big"):
            raise ValueError(f"endian must be 'little' or 'big', got {self.endian!r}")
        if self.pointer_bits not in (16, 32, 64):
            raise ValueError(f"pointer_bits must be 16/32/64, got {self.pointer_bits}")

    @property
    def pointer_bytes(self) -> int:
        return self.pointer_bits // 8

    @property
    def is_freestanding(self) -> bool:
        """No OS underneath: no libc, no syscalls, no process exit."""
        return self.target.startswith("freestanding")

    def asdict(self) -> "dict[str, Any]":
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def __str__(self) -> str:
        return (f"{self.frontend} -> {self.backend} for {self.target}"
                f"{'/' + self.abi if self.abi else ''} "
                f"({self.pointer_bits}-bit {self.endian}-endian)")


def _host() -> CompileData:
    """What the fields mean when no build has fixed them."""
    from ._version import __version__ as version

    return CompileData(
        backend="cpython",
        target=("windows" if sys.platform == "win32"
                else "linux" if sys.platform.startswith("linux")
                else sys.platform),
        abi="",
        arch="",
        pointer_bits=64 if sys.maxsize > 2**32 else 32,
        endian=sys.byteorder,
        frontend="python",
        passes=(),
        compiler_version=version,
        is_compiled=False,
    )


#: The live value. A build replaces it via :func:`_freeze` before codegen, so
#: anything the compiled program reads is the target's answer, not the host's.
COMPILE_DATA: CompileData = _host()


def _freeze(**values: Any) -> CompileData:
    """Fix ``COMPILE_DATA`` for the build now starting. Compiler-internal.

    Not public: a program cannot rewrite its own build description, which is
    the entire point of the type being frozen. The driver calls this once per
    build, before the backend runs.
    """
    global COMPILE_DATA
    known = {f.name for f in fields(CompileData)}
    unknown = set(values) - known
    if unknown:
        raise TypeError(f"unknown COMPILE_DATA field(s): {', '.join(sorted(unknown))}")
    COMPILE_DATA = replace(_host(), is_compiled=True, **values)
    return COMPILE_DATA


__all__ = ["COMPILE_DATA", "CompileData"]
