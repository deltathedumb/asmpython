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

Plugin authoring (codegen backends, linkers, and embedded other-language
source via `mlang`) is organized as one namespace submodule per concern --
each exposes its own registration class(es), accessed off the top-level
package:

    import asmpython

    asmpython.backend.Backend(name="my_backend", impl=...)
    asmpython.linker.Linker(name="my_linker", impl=...)
    asmpython.mlang.Config(...)  # embed/compile another language's source

Each submodule (`asmpython.backend`, `asmpython.linker`, `asmpython.mlang`)
is importable on its own (`import asmpython.backend`) or reached as an
attribute after `import asmpython`, matching ordinary Python package
semantics -- there is no flat top-level `asmpython.Backend`/
`asmpython.Linker` shorthand.

(Compiler-syntax extensions -- `asmpython.extend.Extension(...)` -- were
withdrawn: asmpython's goal is mirroring CPython's language with only tiny,
necessary differences, and letting the grammar itself be extended cut
against that. The withdrawn implementation is preserved for reference under
`archived/extensions/`.)
"""

from __future__ import annotations

import sys
from types import ModuleType

from . import backend, linker, mlang
from ._version import (
    ASMPYTHON_BUILD,
    PACKAGING_VERSION,
    PYTHON_LANGUAGE_VERSION,
    VERSION_INFO,
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
        config = func.__asmpython_config__
        config = {
            "dyn": config.get("dyn", True),
            "gc": config.get("gc", True),
            "exc": config.get("exc", True),
            "refl": config.get("refl", True),
            "free": config.get("free", True),
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
    "ASMPYTHON_BUILD",
    "PACKAGING_VERSION",
    "PYTHON_LANGUAGE_VERSION",
    "VERSION_INFO",
    "backend",
    "import_binary",
    "linker",
    "mlang",
    "__version__",
]
