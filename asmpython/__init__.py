"""asmpython: native Python -> x86-64 -> executable transpiler.

The public surface is small and deliberately mirrors what a user writes:

    from asmpython.assembly import asm_func

`@asm_func` marks a function whose body is raw NASM (the compiler emits it
verbatim). The compiler internals live under the private `_compiler`,
`_runtime`, and `_stdlib` subpackages.

`import_binary()` is a compiler intrinsic when source is compiled by
asmpython. Under ordinary CPython it is resolved lazily to a :mod:`ctypes`
wrapper, so the same DLL/SO declarations can be reference-tested without
shadowing the compiler intrinsic during static import resolution.

Plugin authoring is organized as one namespace submodule per concern, while
installable extension packages use the top-level ``Extension`` descriptor:

    import asmpython
    from asmpython import Extension

    extension = Extension(id="my_extension")
    asmpython.backend.Backend(name="my_backend", impl=...)
    asmpython.linker.Linker(name="my_linker", impl=...)
    asmpython.mlang.Config(...)

Runtime ownership and mixed-traceback APIs live under ``asmpython.runtime``.
Each submodule is importable on its own or reached as an attribute after
``import asmpython``, matching ordinary Python package semantics.
"""
from __future__ import annotations

import sys
from types import ModuleType

from . import backend, linker, mlang, runtime
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


# make it so you can do import asmpython; @asmpython(); def func(): ...
sys.modules[__name__].__class__ = _ASMPythonModule


__all__ = [
    "ASMPYTHON_VERSION",
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
    "import_binary",
    "linker",
    "mlang",
    "runtime",
    "__version__",
]
