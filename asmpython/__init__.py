"""asmpython: native Python -> x86-64 -> executable transpiler.

The public surface is small and deliberately mirrors what a user writes:

    from asmpython.assembly import asm_func

`@asm_func` marks a function whose body is raw NASM (the compiler emits it
verbatim). The compiler internals live under the private `_compiler`,
`_runtime`, and `_stdlib` subpackages.

`import_binary()` is a compiler intrinsic when source is compiled by
asmpython. Under ordinary CPython it mirrors the same decorator-oriented API
on top of :mod:`ctypes`, so code can be reference-tested without maintaining a
second DLL/SO binding layer::

    libc = import_binary("libc.so.6")

    @libc.imported
    def toupper(value: int) -> int:
        pass

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

import ctypes as _ctypes
import functools as _functools
import inspect as _inspect
from collections.abc import Callable as _Callable
from typing import Any as _Any

from . import backend, linker, mlang

__version__ = "3.14-preview"


_EMPTY = _inspect.Signature.empty


def _annotation_name(annotation: _Any) -> str | None:
    """Return the builtin spelling used by the native-import ABI."""
    if annotation is None or annotation is type(None):
        return "None"
    if isinstance(annotation, str):
        name = annotation.strip()
        if name.startswith("builtins."):
            name = name[len("builtins.") :]
        return name
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is bool:
        return "bool"
    if annotation is str:
        return "str"
    if annotation is bytes:
        return "bytes"
    return None


def _ctype_for_annotation(annotation: _Any, *, function: str, parameter: str) -> _Any:
    """Translate the public import annotations to their ctypes ABI types."""
    if annotation is _EMPTY:
        raise TypeError(
            f"imported function {function!r} must annotate {parameter}; "
            "use int, float, bool, str, bytes, None, or a ctypes type"
        )

    name = _annotation_name(annotation)
    if name == "int":
        # asmpython's public int ABI is signed 64-bit on hosted targets.
        return _ctypes.c_int64
    if name == "float":
        return _ctypes.c_double
    if name == "bool":
        return _ctypes.c_bool
    if name in {"str", "bytes"}:
        return _ctypes.c_char_p
    if name in {"None", "NoneType"}:
        return None

    # Allow callers to opt into a more precise CPython-only declaration with
    # normal ctypes scalar/pointer/structure classes. Compiled asmpython code
    # should continue to use its compiler-supported annotations.
    if isinstance(annotation, type) and (
        hasattr(annotation, "_type_") or issubclass(annotation, _ctypes.Structure)
    ):
        return annotation

    raise TypeError(
        f"unsupported annotation {annotation!r} on imported function "
        f"{function!r} ({parameter})"
    )


def _argument_converter(annotation: _Any) -> _Callable[[_Any], _Any]:
    name = _annotation_name(annotation)
    if name == "str":
        return _encode_string
    return _identity


def _result_converter(annotation: _Any) -> _Callable[[_Any], _Any]:
    name = _annotation_name(annotation)
    if name == "str":
        return _decode_string
    return _identity


def _identity(value: _Any) -> _Any:
    return value


def _encode_string(value: _Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"expected str for imported c_char_p argument, got {type(value).__name__}")


def _decode_string(value: _Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


class _ImportedFunction:
    """Callable CPython adapter around one ctypes function export."""

    def __init__(self, stub: _Callable[..., _Any], native: _Any) -> None:
        self._signature = _inspect.signature(stub)
        self._native = native
        self._parameters = list(self._signature.parameters.values())

        ctypes_args: list[_Any] = []
        self._argument_converters: list[_Callable[[_Any], _Any]] = []
        for parameter in self._parameters:
            if parameter.kind not in (
                _inspect.Parameter.POSITIONAL_ONLY,
                _inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                raise TypeError(
                    f"imported function {stub.__name__!r} cannot use "
                    f"{parameter.kind.description} parameter {parameter.name!r}"
                )
            ctypes_args.append(
                _ctype_for_annotation(
                    parameter.annotation,
                    function=stub.__name__,
                    parameter=f"parameter {parameter.name!r}",
                )
            )
            self._argument_converters.append(_argument_converter(parameter.annotation))

        native.argtypes = ctypes_args
        native.restype = _ctype_for_annotation(
            self._signature.return_annotation,
            function=stub.__name__,
            parameter="the return value",
        )
        self._result_converter = _result_converter(self._signature.return_annotation)
        _functools.update_wrapper(self, stub)

    def __call__(self, *args: _Any, **kwargs: _Any) -> _Any:
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        converted: list[_Any] = []
        for parameter, converter in zip(self._parameters, self._argument_converters):
            converted.append(converter(bound.arguments[parameter.name]))
        return self._result_converter(self._native(*converted))


class _ImportedBinary:
    """CPython representation of a native library imported by asmpython."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._library = _ctypes.CDLL(path)

    def imported(self, stub: _Callable[..., _Any]) -> _ImportedFunction:
        """Bind the export named by *stub* and replace the declaration stub."""
        try:
            native = getattr(self._library, stub.__name__)
        except AttributeError as exc:
            raise AttributeError(
                f"native library {self.path!r} has no exported symbol {stub.__name__!r}"
            ) from exc
        function = _ImportedFunction(stub, native)
        setattr(self, stub.__name__, function)
        return function

    def __repr__(self) -> str:
        return f"<import_binary {self.path!r}>"


def import_binary(path: str) -> _ImportedBinary:
    """Load a DLL/SO with ctypes when running under ordinary CPython.

    During native compilation this call and its ``@library.imported``
    declarations remain compiler intrinsics. The CPython implementation exists
    so the same source can be executed as a behavioral reference before or
    alongside an asmpython build.
    """
    if not isinstance(path, str):
        raise TypeError(f"import_binary() path must be str, got {type(path).__name__}")
    return _ImportedBinary(path)


__all__ = ["backend", "linker", "mlang", "import_binary", "__version__"]
