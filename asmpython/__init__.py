"""ASMPython public package surface.

Ordinary Python remains the language contract. Compiler features are exposed
through optional Python APIs, command-line options, installable extensions, and
compiler-visible metadata decorators.
"""
from __future__ import annotations

import sys
from types import ModuleType

from . import annotations, backend, embedded, extras, linker, mlang, runtime
from .capabilities import CapabilitySet, Dependency
from .extension import Extension
from .annotations import *
from .annotations import __all__ as _annotations_all
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
    """Attach per-function compiler options for CPython tooling and ASMPython."""

    def decorator(func):
        previous = getattr(func, "__asmpython_config__", {})
        config = dict(previous) if isinstance(previous, dict) else {}
        config.update(
            {
                "dyn": dyn,
                "gc": gc,
                "exc": exc,
                "refl": refl,
                "free": free,
                "enforced": list(config.get("enforced", [])),
            }
        )
        func.__asmpython_config__ = config
        return func

    return decorator


class _ASMPythonModule(ModuleType):
    def __call__(self, **options):
        return compile_function(**options)


# Supports: import asmpython; @asmpython(...); def func(): ...
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
    "annotations",
    "backend",
    "embedded",
    "extras",
    "import_binary",
    "linker",
    "mlang",
    "runtime",
    "compile_function",
    "__version__",
    *_annotations_all,
]
