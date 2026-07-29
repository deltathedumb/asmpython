"""ASMPython public package surface.

Ordinary Python remains the language contract. Compiler features are exposed
through optional Python APIs, command-line options, installable extensions, and
compiler-visible metadata decorators.
"""
from __future__ import annotations

import builtins
import struct
import sys
from types import ModuleType

from . import (
    annotations, backend, compiler_pass, embedded, extras, frontend, linker,
    mlang, runtime,
)
from .capabilities import CapabilitySet, Dependency
from .compile_data import COMPILE_DATA, CompileData
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


def _bitcast_f2i(value: float) -> int:
    """Raw IEEE-754 bit pattern of a float, reinterpreted as a signed int64.

    Not a numeric conversion (unlike int(x)) -- the compiled path lowers this
    to a bit-identical register move (MOVQ xmm->gp / FMOV on ARM64) via the
    existing bitcast_f2i IR instruction; this is the matching CPython-side
    definition so the same source runs unchanged when imported directly
    (as PortaPy's own generated-module tests do).
    """
    (bits,) = struct.unpack("<q", struct.pack("<d", value))
    return bits


def _bitcast_i2f(value: int) -> float:
    """Reverse of bitcast_f2i: reinterpret a signed int64's bits as a float."""
    (result,) = struct.unpack("<d", struct.pack("<q", value))
    return result


# bitcast_f2i/bitcast_i2f are asmpython builtins (see _compiler/sema.py's
# BUILTINS table and _compiler/ir_lower.py's Call lowering), not real CPython
# builtins -- unlike every other name the compiler recognizes there (ord,
# chr, len, ...). Installing them onto the real `builtins` module the moment
# asmpython is imported keeps the "asmpython source is a strict Python
# subset, runnable unmodified under plain CPython" invariant intact for
# generated modules that get imported directly (not just compiled).
if not hasattr(builtins, "bitcast_f2i"):
    builtins.bitcast_f2i = _bitcast_f2i
if not hasattr(builtins, "bitcast_i2f"):
    builtins.bitcast_i2f = _bitcast_i2f


__all__ = [
    "COMPILE_DATA",
    "CompileData",
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
    "compiler_pass",
    "embedded",
    "extras",
    "frontend",
    "import_binary",
    "linker",
    "mlang",
    "runtime",
    "compile_function",
    "__version__",
    *_annotations_all,
]
