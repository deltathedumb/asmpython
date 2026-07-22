"""ASMPython's public package API.

Common compiler metadata is intentionally available directly from the package::

    from asmpython import Public, access, abi, C, const, owned

The same names remain available from :mod:`asmpython.extras` for compatibility.
Compiler internals live under private ``_compiler`` and ``_runtime`` packages.
"""
from __future__ import annotations

import sys
from types import ModuleType

from . import backend, extras, linker, mlang
from .extras import *
from .extras import __all__ as _extras_all

__version__ = "3.14-preview"


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
    "backend",
    "extras",
    "linker",
    "mlang",
    "import_binary",
    "compile_function",
    "__version__",
    *_extras_all,
]
