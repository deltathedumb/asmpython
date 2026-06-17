"""platform module: platform information.

Reports the asmpython compilation target. On a Windows-compiled binary
the platform is 'windows'; on Linux it is 'linux'.
"""
from __future__ import annotations

import sys


def system() -> str:
    """Return OS name: 'Windows', 'Linux', or 'Unknown'."""
    p: str = sys.platform
    if p == "win32":
        return "Windows"
    if p == "linux":
        return "Linux"
    return "Unknown"


def node() -> str:
    """Return hostname (stub, returns empty string)."""
    return ""


def release() -> str:
    """Return OS release (stub)."""
    return ""


def version() -> str:
    """Return OS version string (stub)."""
    return ""


def machine() -> str:
    """Return machine type: 'x86_64'."""
    return "x86_64"


def processor() -> str:
    """Return processor type: 'x86_64'."""
    return "x86_64"


def architecture() -> list:
    """Return [bits, linkage] e.g. ['64bit', 'ELF']."""
    p: str = sys.platform
    if p == "win32":
        return ["64bit", "WindowsPE"]
    return ["64bit", "ELF"]


def python_version() -> str:
    """Return the asmpython version string."""
    return sys.version


def python_implementation() -> str:
    """Return 'asmpython'."""
    return "asmpython"


def uname() -> list:
    """Return [system, node, release, version, machine, processor]."""
    return [system(), node(), release(), version(), machine(), processor()]


def platform(aliased: int = 0, terse: int = 0) -> str:
    """Return a string identifying the platform."""
    s: str = system()
    if terse == 1:
        return s
    return s + "-x86_64"
