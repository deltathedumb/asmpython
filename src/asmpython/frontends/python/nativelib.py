"""Declaring a native library, so a program can `import` one.

    asmpython build app.py --native-library libs.json

    # libs.json
    [{"module": "user32", "library": "user32.dll",
      "functions": [{"name": "GetSystemMetrics",
                     "args": ["c_int"], "ret": "c_int"}]}]

    # app.py
    import user32
    print(user32.GetSystemMetrics(0))

## What this is

A way to say "this shared library exists, here is what I want to call in it,
and here is the name I want to import it under". It is the mechanism the
pre-rewrite compiler had as `native_libraries` in `project.json`, restored --
and `archived/docs/NATIVE_LIBRARIES.md` is still an accurate description of
what it is FOR, because the problem has not changed: without it, using a
library the compiler did not ship with meant editing the compiler.

## Why it is barely any code

**A DECLARED FUNCTION IS A `ctypes` FUNCTION THAT ALREADY HAS ITS
`argtypes`.** That is the whole insight, and it is why this module models a
declaration and nothing else. `frontends/python/cffi.py` and the ctypes path
in `analysis.py` already: check an argument against a machine type, convert a
dynamic one at the call, emit ONE `Op.CALL` to an external symbol, and hand
the library to the linker. None of that is specific to how the signature was
learned.

So `import user32` does exactly what these three lines would have done:

    user32 = ctypes.CDLL("user32.dll")
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int

and every path after that point is shared, tested code. A separate
implementation would be a second way to type-check a native call, and the way
two of those drift is one of them getting an integer width wrong.

## What a declaration cannot do

**IT CANNOT SHADOW A MODULE THAT ALREADY RESOLVES.** Pointing `import math`
at a DLL is refused, not silently honoured -- the same rule the legacy
mechanism had, and for the same reason `bundled.py` gives for the standard
library winning a name.

**IT IS A C ABI ONLY.** No C++ mangling, no vtables, no exceptions: export an
`extern "C"` wrapper and declare that. And it is NOT a CPython extension
module -- a `.pyd` built against CPython calls back into `PyObject`,
refcounting and `PyArg_ParseTuple`, which is a different and much larger
piece of work. `E0129` says so where it comes up.

**A SIGNATURE IS REQUIRED, exactly as it is for `ctypes`.** There is nothing
to read a foreign symbol's argument kinds out of, and guessing is how a
native call corrupts a stack rather than failing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import cffi

#: The declaration vocabulary. `cffi.TYPES` is the real one -- these are the
#: spellings the LEGACY mechanism used, kept because its documentation is
#: still the description of this feature and a reader following it should not
#: be told the words it uses do not exist.
ALIASES: dict[str, str] = {
    "int": "c_int",
    "long": "c_long",
    "float": "c_double",
    "double": "c_double",
    "str": "c_char_p",
    "ptr": "c_void_p",
    "void_p": "c_void_p",
    "bool": "c_bool",
}


class DeclarationError(Exception):
    """A declaration that cannot be read. Carries what was wrong with it."""


@dataclass(frozen=True)
class NativeFunction:
    """One callable a declared library exposes.

    `params` and `ret` are `cffi.TYPES` keys, so they are the same vocabulary
    an `argtypes` list is written in and reach the same table.
    """

    name: str
    params: tuple[str, ...] = ()
    ret: str = cffi.DEFAULT_RESTYPE
    #: The C symbol, when it differs from the name the program imports.
    symbol: str | None = None

    @property
    def c_symbol(self) -> str:
        return self.symbol or self.name


@dataclass(frozen=True)
class NativeLibrary:
    """A shared library, and the module name a program reaches it under.

    `library` is the LOAD name -- `user32.dll`, `libm.so.6` -- spelled the way
    `ctypes.CDLL` would spell it, because `cffi.link_flag` already knows how
    to turn that into what a linker wants and there is no reason for two
    conventions. `module` is what the program writes after `import`.
    """

    module: str
    library: str
    functions: tuple[NativeFunction, ...] = ()
    #: `"windows"` / `"linux"`, or None to apply to every target. A
    #: cross-platform program declares each platform's library separately and
    #: gives them ONE module name; which one provides a symbol is settled per
    #: target rather than by the program.
    target_os: str | None = None

    def member(self, name: str) -> NativeFunction | None:
        for fn in self.functions:
            if fn.name == name:
                return fn
        return None


@dataclass
class Registry:
    """Every library declared for the compilation running now.

    KEYED BY MODULE NAME, not by library: two libraries sharing a module name
    is the cross-platform case above, and the one that applies to the target
    wins. A module name nothing declared is simply absent, which is what makes
    `import` fall through to the ordinary rules.
    """

    _by_module: dict[str, list[NativeLibrary]] = field(default_factory=dict)

    def add(self, library: NativeLibrary) -> None:
        self._by_module.setdefault(library.module, []).append(library)

    def get(self, module: str, target_os: str | None = None
            ) -> NativeLibrary | None:
        """The declaration for `module` that applies to `target_os`.

        AN UNSCOPED DECLARATION IS THE FALLBACK, never the winner: a
        declaration naming this platform is more specific than one naming
        none, so it is preferred however they were written down.
        """
        found = self._by_module.get(module)
        if not found:
            return None
        for library in found:
            if library.target_os and library.target_os == target_os:
                return library
        for library in found:
            if not library.target_os:
                return library
        return None

    def modules(self) -> list[str]:
        return sorted(self._by_module)

    def all(self) -> list[NativeLibrary]:
        return [lib for group in self._by_module.values() for lib in group]

    def __bool__(self) -> bool:
        return bool(self._by_module)


#: The registry in force for the compilation running now.
#:
#: A MODULE GLOBAL, matching `imports.use` and `modules.use_backend`. The
#: frontend is handed a source and a sink and nothing else, so anything the
#: driver knows and the frontend needs arrives this way. Replaced rather than
#: added to on every compilation, so two in one process cannot see each
#: other's declarations.
_CURRENT = Registry()

#: The platform the declarations are being chosen for, as the target's own
#: `os` -- published beside the registry because a scoped declaration is only
#: meaningful against one, and asking the target registry from the frontend
#: would be the frontend learning which backend is compiling.
_TARGET_OS: str | None = None


def use(registry: "Registry | None", target_os: str | None = None) -> None:
    global _CURRENT, _TARGET_OS
    _CURRENT = registry or Registry()
    _TARGET_OS = target_os


def current() -> Registry:
    return _CURRENT


def target_os() -> str | None:
    return _TARGET_OS


# ── reading a declaration ───────────────────────────────────────────────────

def type_named(raw: str) -> str:
    """A declared type, as a `cffi.TYPES` key. Raises on anything else."""
    name = ALIASES.get(raw, raw)
    if name not in cffi.TYPES:
        raise DeclarationError(
            f"{raw!r} is not a native type; use one of "
            + ", ".join(sorted(cffi.TYPES)))
    return name


def function_from(data: dict) -> NativeFunction:
    if not isinstance(data, dict):
        raise DeclarationError("each function must be an object")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise DeclarationError("a function needs a 'name'")
    raw_args = data.get("args", data.get("argtypes", ()))
    if isinstance(raw_args, str) or not isinstance(raw_args, (list, tuple)):
        raise DeclarationError(f"{name}: 'args' must be a list")
    # A SIGNATURE IS REQUIRED AND NOT GUESSED, exactly as it is for `ctypes`.
    # `args: []` is a declaration that the function takes none, which is a
    # different statement from leaving it out -- and leaving it out is the one
    # this refuses, because that is where a stack gets corrupted.
    if "args" not in data and "argtypes" not in data:
        raise DeclarationError(
            f"{name}: no 'args'; this compiler will not guess a native "
            f"signature. Write 'args': [] for a function that takes none")
    params = tuple(type_named(str(a)) for a in raw_args)
    ret = type_named(str(data.get("ret", data.get("restype",
                                                  cffi.DEFAULT_RESTYPE))))
    symbol = data.get("symbol")
    if symbol is not None and not isinstance(symbol, str):
        raise DeclarationError(f"{name}: 'symbol' must be a string")
    return NativeFunction(name=name, params=params, ret=ret, symbol=symbol)


def library_from(data: dict) -> NativeLibrary:
    if not isinstance(data, dict):
        raise DeclarationError("each declaration must be an object")
    library = data.get("library", data.get("name"))
    if not isinstance(library, str) or not library:
        raise DeclarationError("a declaration needs a 'library'")
    module = data.get("module")
    if not isinstance(module, str) or not module:
        raise DeclarationError(
            f"{library}: a declaration needs a 'module' -- the name the "
            f"program writes after `import`")
    if not module.isidentifier():
        raise DeclarationError(f"{module!r} is not a usable module name")
    target_os = data.get("target_os")
    if target_os is not None and not isinstance(target_os, str):
        raise DeclarationError(f"{module}: 'target_os' must be a string")
    functions = tuple(function_from(f) for f in data.get("functions", ()))
    return NativeLibrary(module=module, library=library,
                         functions=functions, target_os=target_os)


def read(path: Path) -> Registry:
    """A declaration file: a list of libraries, or one on its own.

    Never partially applied. A file with a bad entry raises rather than
    registering the entries before it -- half a declaration file is a program
    that compiles and links against a library nobody meant.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise DeclarationError(f"cannot read {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise DeclarationError(f"{path} is not valid JSON: {exc}") from None
    entries = data if isinstance(data, list) else [data]
    registry = Registry()
    for entry in entries:
        registry.add(library_from(entry))
    return registry
