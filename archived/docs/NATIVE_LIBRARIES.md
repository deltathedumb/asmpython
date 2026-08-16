# Linking against external native libraries

asmpython can link against, and call into, shared libraries it did not ship
with — `SDL2.dll`, `libopenblas.so.0`, `sqlite3.dll`, anything exposing a C
ABI. This page covers how to declare one.

## The problem this replaces

Both linkers decide which shared library provides an undefined symbol from a
hardcoded table: `_DLL_FOR_SYMBOL` in `_backends/x86_64/pe_linker.py`, and
`_SO_FOR_SYMBOL` in `_backends/x86_64/elf_linker.py`. Those tables cover
exactly the externs asmpython's own runtime references. Anything else used to
fail with *"add it to `pe_linker._DLL_FOR_SYMBOL` if it's a real import"* —
so using a new library meant **editing the compiler**.

Declaring the library is now the supported path.

## Quick version: link-only

If you only need symbols to resolve — typically because another library you
link references them — one flag is enough:

```
asmpython build app.py --link-library SDL2.dll=vendor/SDL2.dll
```

Accepted forms:

| Form | Meaning |
| --- | --- |
| `SDL2.dll` | load name; exports read from a file of that name |
| `SDL2.dll=vendor/SDL2.dll` | load name, exports read from an explicit path |
| `SDL2.dll:SDL_Init,SDL_Quit` | load name, exactly these symbols |

The name on the left is the **load name** — what the OS loader resolves at run
time, recorded in the PE import table or the ELF `DT_NEEDED` list. It does not
have to match the file the exports are read from at build time, because the
machine you build on is not necessarily the machine you ship to.

Exports are discovered by reading the file's own export table (PE `.edata`, or
ELF `.dynsym`), so naming one library does not mean transcribing a few thousand
symbol names by hand.

## Full version: calling into the library

Linking decides where a symbol comes from. To *call* a function, sema also
needs its signature — asmpython cannot infer the argument or return kinds of a
foreign symbol. Declare those under `native_libraries` in `project.json`:

```json
{
  "name": "screeninfo",
  "entry": "main.py",
  "target": ["windows"],
  "native_libraries": [
    {
      "name": "user32.dll",
      "path": "C:/Windows/System32/user32.dll",
      "target_os": "windows",
      "module": "user32",
      "functions": [
        {"name": "GetSystemMetrics", "args": ["int"], "ret": "int"}
      ]
    }
  ]
}
```

`module` is the name your source imports. The functions become ordinary FFI
bindings, so they type-check and lower through exactly the same path as a
built-in binding:

```python
import user32

print(user32.GetSystemMetrics(0))   # screen width
```

### Entry fields

| Field | Meaning |
| --- | --- |
| `name` | **required** — load name recorded in the built binary |
| `path` | file to read exports from; omit if you list `symbols` |
| `symbols` | explicit symbol list; suppresses discovery |
| `target_os` | `"windows"` or `"linux"`; omit to apply to both |
| `module` | module name your source imports; required if `functions` is set |
| `functions` | callables, each `{name, args, ret, symbol?, ret_conv?}` |

`args` and `ret` use asmpython's FFI kinds: `"int"`, `"float"`, `"str"`. Set
`symbol` when the C name differs from the name you want in Python, and
`ret_conv` to `"ptr"` for a real 64-bit pointer return (an `int` return is
sign-extended from EAX, which truncates a heap address) or `"f2i"` for a
double the caller wants truncated to int.

A cross-platform project declares each platform's library separately, scoped by
`target_os`, sharing one `module` name. Both halves are visible while type
checking; which library actually provides a symbol is settled per-target at
link time.

## Precedence, and what a declaration cannot do

- **The builtin tables always win.** A declaration can only add a mapping for a
  symbol the linker did not already know. Declaring a library that exports
  `malloc` cannot retarget `malloc` away from the C runtime.
- **A build that declares nothing is unaffected**, byte-for-byte. This is
  verified by linking a corpus case with and without the mechanism present and
  comparing the output hashes.
- **Between two declared libraries** claiming the same symbol, the first
  declaration wins — matching how a real linker resolves a duplicate across
  `-l` flags in command-line order.
- **A declared module cannot shadow a stdlib module.** Pointing `import math`
  at your own DLL is refused rather than silently honoured.

## What this does *not* do

This mechanism links against libraries with a **C ABI**. It does not:

- **Compile C or C++ source.** `--frontend c` is still a scaffold that raises
  `NotImplementedError`.
- **Load CPython extension modules.** A `.pyd`/`.so` built against CPython
  calls the CPython C-API (`PyObject*`, refcounting, `PyArg_ParseTuple`), which
  the native runtime does not implement. `_backends/host_site_packages.py`
  rejects those explicitly. numpy, torch, and scipy are all in this category —
  supporting them is a separate and much larger piece of work.
- **Handle C++ name mangling, vtables, or exceptions.** Export a C wrapper
  (`extern "C"`) and declare that.
