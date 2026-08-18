"""`ctypes`: calling a native library, resolved at COMPILE time.

    import ctypes
    libm = ctypes.CDLL("m")
    libm.sqrt.restype = ctypes.c_double
    libm.sqrt.argtypes = [ctypes.c_double]
    print(libm.sqrt(9.0))

## Why this needs no dlopen, and therefore no new platform function

The obvious implementation is `dlopen`/`dlsym`, and it would be a FOURTH and
FIFTH function every backend must supply -- against a floor stage 2 of
`docs/INERT-RUNTIME.md` spent its whole argument getting down to three. It is
also not needed, and that was measured before any of this was written: the C
backend already emits

    extern double sqrt(double r0);

for any external the IR declares and does not define, and the toolchain
resolves it. A hand-written IR module calling `sqrt` linked with `-lm` and
printed `3.0` on the first try. **The mechanism was entirely there; what was
missing was a way to say it in Python.**

So `ctypes.CDLL("m")` is not a load. It is a promise to the LINKER, and
`libm.sqrt(...)` is an ordinary `Op.CALL` to the symbol `sqrt`. The library
name becomes `-lm`. Nothing happens at run time that would not happen in a C
program calling the same function.

This is the same trick `bundled.py` plays for the standard library and
`backends/jvm/interop.py` plays for Java: resolve names while compiling, emit
direct calls, and have no run-time machinery at all.

## What that costs, stated plainly

**THE LIBRARY AND THE SYMBOL MUST BE LITERALS.** `CDLL(name)` for a computed
`name` genuinely cannot work this way -- there is no symbol to hand the linker
-- and it is refused with a message saying so rather than half-supported. That
is the one case `dlopen` would buy, and buying it costs the floor; see
`E0112`.

**A SIGNATURE IS REQUIRED, and this is stricter than CPython.** There, a
missing `argtypes` means "guess from the values" and a missing `restype` means
`c_int`. Guessing is exactly how a ctypes program corrupts a stack: pass a
Python float where the callee wants a double-by-value on a register-poor ABI
and the answer is silently wrong. A compiler that knows the types at build
time has no reason to guess, so it does not.

**NO STRUCTS, ARRAYS, `byref`, CALLBACKS OR `errcheck` YET.** Scalars and
pointers. A callback needs `func_addr`/`call_ptr`, which is deferred for the
fourth time and for the same reason each time.

**A POINTER ARGUMENT MAY BE `str` OR `bytes`, WHERE CPYTHON WANTS `bytes` FOR
`c_char_p`.** That follows from the flattening above: every pointer type is
one machine word here, so nothing distinguishes `c_char_p` from `c_wchar_p` to
act on. A `str` is passed as its UTF-8 bytes, which is what a `char *` API
wants; CPython would raise `ArgumentError`. Leniency, not a different answer --
but a program written against this and run under CPython would need `b"..."`.

**A SYMBOL THE RUNTIME'S OWN HEADERS ALREADY DECLARE CANNOT TAKE A POINTER.**
`objects.py` includes `<stdio.h>`, `<stdlib.h>`, `<string.h>`, `<math.h>` and
`<errno.h>`, and the backend emits `uint64_t strlen(uintptr_t)` for a symbol
the IR declares -- which gcc rejects against `size_t strlen(const char *)`.
Scalar signatures are fine, because `double sqrt(double)` matches what the IR
emits; POINTER ones are not, because the IR has one pointer type and C has
many. So `sqrt` works and `strlen`, `fopen`, `getenv` do not.

IT IS THE HEADER AND NOT THE LIBRARY, which is a smaller rule than it first
looks and worth stating precisely: a libc symbol whose declaration this
runtime never sees is reachable like any other. `<io.h>` and `<fcntl.h>` are
NOT included, so `_open`, `_read`, `_write`, `_close` and `_lseek` -- the
whole low-level file surface, pointers and all -- work today, and
`bundled/pathlib.py` is built on them. `_unlink` and `_mkdir` do not, because
MinGW declares those two in `<stdio.h>`; that module reaches kernel32's
`DeleteFileA` and `CreateDirectoryA` instead, which are in no header this
includes. The general fix is for the backend to CALL THROUGH A CAST rather
than declare an extern, which is a change to `backends/c/emit.py` and not to
this file.

**AN INT IS ACCEPTED WHERE A POINTER IS DECLARED, and means the address
itself.** C's own rule for a pointer parameter admits a null pointer constant,
and `CreateDirectoryA(path, 0)` is the ordinary way to write "no security
descriptor". Refusing it made every native call with a NULL argument a
TypeError. A float or a list is still refused, because the alternative is
handing a callee whatever the object's payload happens to be.

**THE IR INTERPRETER RESOLVES NONE OF THIS, so a short list of symbols is
bound by hand.** Everything above happens at COMPILE and LINK time; the
interpreter has no linker, so a declared symbol trapped there with `call to
undefined function`. Since the interpreter is the oracle the C backend is
measured against, that left any module using `ctypes` with no check on its
compiled behaviour at all. `ir/natives_host.py` binds the symbols the bundled
library declares, through `os`, marshalling each pointer between a host object
and interpreter memory. It grows when the standard library does; it is not an
attempt to bind libc.
"""
from __future__ import annotations

import ast

#: `ctypes` scalar types, and the IR type each is.
#:
#: NAMED FROM THE C SIDE, not from Python's. `c_int` is 32 bits on every
#: platform asmpython targets, and calling it `int` because Python spells its
#: own integers that way is how a 64-bit value gets passed to a function
#: expecting 32 -- which does not fail, it truncates.
TYPES: dict[str, str] = {
    "c_bool": "u8",
    "c_char": "i8", "c_byte": "i8", "c_ubyte": "u8",
    "c_short": "i16", "c_ushort": "u16",
    "c_int": "i32", "c_uint": "u32",
    "c_long": "i32", "c_ulong": "u32",
    "c_longlong": "i64", "c_ulonglong": "u64",
    "c_int8": "i8", "c_uint8": "u8",
    "c_int16": "i16", "c_uint16": "u16",
    "c_int32": "i32", "c_uint32": "u32",
    "c_int64": "i64", "c_uint64": "u64",
    "c_ssize_t": "i64", "c_size_t": "u64",
    "c_float": "f32", "c_double": "f64",
    #: Every pointer is one machine word and the IR has one pointer type, so
    #: the distinctions `ctypes` draws between them are not ones a backend can
    #: act on. They are accepted and flattened, which is what the ABI does too.
    "c_void_p": "ptr", "c_char_p": "ptr", "c_wchar_p": "ptr",
}

#: What `ctypes` uses when a function has no `restype` set. Honoured because
#: it is the documented default and a program that relies on it is not wrong
#: -- unlike a missing `argtypes`, which is a guess this refuses to make.
DEFAULT_RESTYPE = "c_int"

#: The names `CDLL` goes by. `WinDLL` and `OleDLL` differ from `CDLL` only in
#: calling convention and error handling on 32-bit Windows; on the 64-bit
#: targets asmpython has, there is one convention and they are the same thing.
LOADERS = frozenset({"CDLL", "WinDLL", "OleDLL", "PyDLL", "LibraryLoader"})


def type_name(node, imported: dict) -> str | None:
    """The `ctypes` type an annotation-ish expression names, or None.

    Accepts both spellings a program may use -- `ctypes.c_double` when the
    module was imported whole, and a bare `c_double` when it was imported from
    -- because both are ordinary and neither is more correct.
    """
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if imported.get(node.value.id) == "ctypes" and node.attr in TYPES:
            return node.attr
        return None
    if isinstance(node, ast.Name) and imported.get(node.id) == "ctypes.type":
        return node.id if node.id in TYPES else None
    return None


def loader_call(node, imported: dict) -> str | None:
    """The library name `CDLL("m")` names, or None if this is not one.

    Returns the empty string for a call whose argument is not a literal, which
    the caller reports -- distinguishing "not a CDLL" from "a CDLL nobody can
    resolve" matters, because only the second is an error.
    """
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if imported.get(func.value.id) != "ctypes" or func.attr not in LOADERS:
            return None
    elif isinstance(func, ast.Name):
        if imported.get(func.id) != "ctypes.loader" or func.id not in LOADERS:
            return None
    else:
        return None
    if len(node.args) != 1:
        return ""
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return ""


def link_flag(library: str) -> str:
    """What to hand the toolchain for a library named the way `ctypes` names it.

    `CDLL("m")`, `CDLL("libm.so.6")` and `CDLL("user32.dll")` all mean the same
    kind of thing and none of them is what a linker wants: it wants `-lm` and
    `-luser32`. The decoration goes, because a program that names a soname is
    naming the same library as one that does not.
    """
    name = library
    for prefix in ("lib",):
        if name.startswith(prefix):
            name = name[len(prefix):]
    for suffix in (".dll", ".dylib"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    # `libm.so.6` -> `m`. Split at `.so` rather than stripping a suffix,
    # because the version follows it.
    if ".so" in name:
        name = name.split(".so", 1)[0]
    return "-l" + name


#: Libraries the last compilation named, for the driver to hand the linker.
#:
#: A MODULE GLOBAL, matching `modules.use_backend` and `imports.use`. The
#: frontend returns a Module and nothing else, so anything the driver needs to
#: learn from it arrives this way. Replaced rather than added to on every
#: compilation, so two in one process cannot see each other's libraries.
_NAMED: tuple = ()


def name_libraries(libraries) -> None:
    global _NAMED
    _NAMED = tuple(libraries)


def named_libraries() -> tuple:
    return _NAMED
