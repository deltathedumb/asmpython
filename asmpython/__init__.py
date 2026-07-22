"""ASMPython public package surface.

Ordinary Python remains the language contract. Compiler features are exposed
through optional Python APIs, command-line options, and installable extensions.
"""
from __future__ import annotations

import sys
from types import ModuleType

from . import backend, embedded, linker, mlang, runtime
from .capabilities import CapabilitySet, Dependency
from .extension import Extension
from ._version import (
    ASMPYTHON_VERSION,
    FULL_VERSION,
    FULL_VERSION_INFO,
    PACKAGING_VERSION,
    PYTHON_LANGUAGE_VERSION,
    PYTHON_VERSION_INFO,
    RELEASE_VERSION,
    VERSION_INFO,
    asmpython_version,
    full_version,
    python_version,
    __version__,
)


def __getattr__(name: str):
    """Resolve CPython-only host adapters without defining compiler intrinsics."""
    if name == "import_binary":
        from ._host_import_binary import import_binary
        return import_binary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted([*globals(), "import_binary"])


def compile_function(
    *,
    dyn: bool = True,
    gc: bool = True,
    exc: bool = True,
    refl: bool = True,
    free: bool = False,
):
    def decorator(func):
        config = getattr(func, "__asmpython_config__", {})
        func.__asmpython_config__ = {
            "dyn": config.get("dyn", dyn),
            "gc": config.get("gc", gc),
            "exc": config.get("exc", exc),
            "refl": config.get("refl", refl),
            "free": config.get("free", free),
            "enforced": config.get("enforced", []),
        }
        return func
    return decorator


class _ASMPythonModule(ModuleType):
    def __call__(self, **options):
        return compile_function(**options)


sys.modules[__name__].__class__ = _ASMPythonModule


__all__ = [
    "ASMPYTHON_VERSION",
    "CapabilitySet",
    "Dependency",
    "Extension",
    "FULL_VERSION",
    "FULL_VERSION_INFO",
    "PACKAGING_VERSION",
    "PYTHON_LANGUAGE_VERSION",
    "PYTHON_VERSION_INFO",
    "RELEASE_VERSION",
    "VERSION_INFO",
    "asmpython_version",
    "full_version",
    "python_version",
    "backend",
    "embedded",
    "import_binary",
    "linker",
    "mlang",
    "runtime",
    "__version__",
]
