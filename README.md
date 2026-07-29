# asmpython

**A Pixelated Dream project.** asmpython compiles Python source to native
x86-64 executables via NASM — no VM, no interpreter, no pip dependencies at
runtime. Write `.py`, get `.exe` or ELF.

```sh
python -m asmpython hello.py          # compile for your host platform
./hello                               # or hello.exe on Windows
```

Current release: **3.14.0-preview**.
See [roadmap.md](roadmap.md) for the full version plan.

---

## Installation

There is no PyPI package for asmpython yet. Install from a checkout of this
repo instead (editable, for development):

```sh
git clone "https://github.com/deltathedumb/asmpython.git"
cd asmpython
pip install -e .
```

This installs the `asmpython` command (and `python -m asmpython`). The
compiler itself is pure Python with no runtime dependencies — but it shells
out to **`nasm`** and **`gcc`** to assemble and link, so make sure both are
on your `PATH` (see [Toolchain requirements](#toolchain-requirements)). On
Windows, `asmpython.bat` from this repo can fetch a portable NASM/MinGW for
you instead.

---

## Targets

| Target | Output | Requires |
| ------ | ------ | -------- |
| `windows` (default on Windows) | PE64 `.exe` | nasm, gcc (MinGW) |
| `linux` (default on Linux) | ELF64 | nasm, gcc |
| `freestanding` | Multiboot1 flat binary | nasm only — boots in QEMU |
| `freestanding16` | Raw 512-byte BIOS MBR + payload | nasm only |

The freestanding target produces a bare-metal kernel binary with no libc, no
OS, and no linker step. It includes a VGA text-mode runtime, COM1 serial
output, a bump allocator, and a long-mode setup stub. The `freestanding16`
target writes a raw BIOS MBR image that transitions real mode → protected
mode → long mode entirely in the output binary.

---

## Quick start

### Windows

```bat
asmpython.bat hello.py
```

Dependencies (nasm, gcc/MinGW) are downloaded automatically on first use.

### Linux

```sh
# Install: python3, gcc, nasm
python -m asmpython hello.py
```

### Freestanding (QEMU)

```sh
python -m asmpython kernel.py --target freestanding -o kernel.bin
qemu-system-x86_64 -kernel kernel.bin -serial stdio -display none
```

---

## CLI reference

```text
python -m asmpython <source.py> [options]

  -o <path>              output path (default: source stem + platform ext)
  --target <t>           windows | linux | freestanding | freestanding16
  --emit-asm             write .asm only, do not assemble or link
  --keep                 keep intermediate .obj / .o files
  --check                front-end diagnostics only (no codegen)
  --json                 machine-readable JSON diagnostics on stderr
  --no-pyinbin-fallback  fail on native rejection instead of interpreting
  --explain <CODE>       print error-code description and exit
  --use-runtime-lib      link pre-built libasmpython_rt (smaller .asm)
  --onefile              single statically-linked binary (default)
  --onedir               exe + shared runtime library in a bundle directory
  --type executable      produce an executable (default)
  --type library         produce a shared library (.dll / .so)
  --icon <path>          embed .ico/.png as exe icon resource (Windows only)
  --nasm <path>          override nasm executable path
  --gcc <path>           override gcc executable path
```

Every diagnostic includes an error code in brackets (e.g. `[E002]`). Pass it
to `asmpython --explain <CODE>` for a full description, or use `--check
--json` for machine-readable output in editor integrations.

### Ecosystem differential testing

The installed `asmpython-pytest-scout` command finds or accepts pytest
repositories, clones them, and compares the same pytest launcher under CPython,
a native asmpython build, and pyinbin. It prints unified output diffs and saves
a JSON report. Because cloned tests execute untrusted code, execution requires
an explicit acknowledgement:

```sh
asmpython-pytest-scout --repo owner/project --allow-untrusted-code
```

See [docs/PYTEST-SCOUT.md](docs/PYTEST-SCOUT.md) for discovery, dependency,
isolation, timeout, and engine-selection options. Native differential builds
use `asmpython build --no-pyinbin-fallback` so a pyinbin fallback can never be
misreported as a compiled executable.

---

## Standard library

### Built-in modules

| Module | Key symbols |
| ------ | ----------- |
| `math` | `sqrt`, `sin`, `cos`, `log`, `pow`, `pi`, `e`, … (22 functions) |
| `os` | `system`, `getenv`, `_exit`, `fopen`/`fgetc`/`fclose`, `access` |
| `sys` | `exit`, `getpid`, `getenv`, `abort`, `version`, `maxsize` |
| `time` | `time`, `sleep`, `clock`, `difftime` |
| `random` | `seed`, `rand`, `RAND_MAX` |
| `io` | `StringIO`, `BytesIO`, `FileIO`, `TextIOWrapper` |
| `pathlib` | `Path` (`.exists`, `.read_text`, `.write_text`, `/` operator, …) |
| `json` | `loads`, `dumps` |
| `struct` | `pack`, `unpack`, `calcsize` |
| `enum` | `Enum`, `IntEnum`, `IntFlag`, `auto` |
| `fractions` | `Fraction` (full arithmetic via dunder dispatch) |
| `contextlib` | `suppress`, `nullcontext`, `closing`, `ExitStack` |
| `collections` | `deque`, `Counter`, `defaultdict`, `OrderedDict` |
| `statistics` | `mean`, `median`, `stdev`, `variance` |
| `uuid` | `uuid4` |
| `argparse` | `ArgumentParser`, `add_argument`, `parse_args` (partial) |

### asmlib — network and GUI

`asmlib` is part of the standard library. Import its modules directly:

```python
from asmlib import network, gui
```

#### `asmlib.network`

TCP client/server using OS sockets.

```python
from asmlib.network import TcpClient

client = TcpClient("example.com", 80)
client.send("GET / HTTP/1.0\r\n\r\n")
resp = client.recv(4096)
client.close()
```

#### `asmlib.gui`

Win32 native window with a software renderer — no SDL, no Qt.

```python
from asmlib import gui

win = gui.Window("My App", 800, 600)
win.set_icon("app.ico")

while win.is_open():
    ev = win.poll_event()
    win.clear(0x1E1E2E)
    win.draw_rect(10, 10, 100, 50, 0xFF4444)
    win.draw_text("Hello GUI", 20, 20, 0xFFFFFF)
    win.present()
```

---

## Language features

### Types

- **`int`** — 64-bit signed
- **`float`** — IEEE-754 double; auto-promoted in mixed arithmetic; true
  division always returns float
- **`str`** — nul-terminated UTF-8; supports concat (`+`), repeat (`*`),
  comparison, ordering, indexing, slicing (with step), `in`/`not in`,
  iteration, and all common methods
- **`bool`** / **`None`** — aliases for `1` / `0`

### String methods

`upper`, `lower`, `strip`/`lstrip`/`rstrip`, `startswith`, `endswith`,
`find`, `count`, `replace`, `split` (with optional `sep` and `maxsplit`),
`rsplit`, `join`, `splitlines`, `partition`, `isdigit`, `isalpha`, `isspace`,
`isupper`, `islower`

### Collections

- **`list`** — dynamic capacity; `int`, `str`, `float`, and instance
  elements; `.append`, `.pop`, `.copy`, indexing, slicing, negative indices,
  iteration, comprehensions, `*` unpack in calls
- **`dict`** — open-addressed hashtable; `str` keys; `.get`, `.keys`,
  `.values`, `.items`, `.update`, `.pop`, iteration, comprehensions
- **`set`** — membership testing, `.add`, `.remove`, `frozenset`, set
  operators (`|`, `&`, `-`, `^`)
- **Tuples** — unpacking assignment, `for k, v in pairs:`, `enumerate`,
  `zip`, heterogeneous-element tuples as return values

### Control flow

`if`/`elif`/`else`, `while`, `for … in range/list/dict/str/tuple/enumerate/
zip/custom-iter`, `break`, `continue`, `pass`, ternary `a if c else b`,
`match`/`case` (structural pattern matching)

### Functions

Default arguments, `*args`, `**kwargs`, type annotations (parsed, not
enforced), closures, `lambda`, decorators, first-class functions, `super()`

### Classes

Single inheritance, `__init__`, instance attributes (any type), method
dispatch, `@classmethod`, `@staticmethod`, `@property` (getter + setter),
`isinstance`, `hasattr`/`getattr`/`setattr`, `super()`

#### Dunder / operator protocol

All standard dunder methods dispatch to user-defined implementations when
the class defines them:

- **Arithmetic** — `__add__`/`__radd__`, `__sub__`/`__rsub__`,
  `__mul__`/`__rmul__`, `__truediv__`/`__rtruediv__`, `__floordiv__`,
  `__mod__`, `__pow__`, `__matmul__` (all with reflected variants)
- **Bitwise** — `__and__`, `__or__`, `__xor__`, `__lshift__`,
  `__rshift__` (+ reflected)
- **Unary** — `__neg__`, `__pos__`, `__invert__`;
  `__abs__` via `abs()`; `__hash__` via `hash()`
- **Comparison** — `__eq__`/`__ne__`, `__lt__`/`__le__`/`__gt__`/`__ge__`
  (with reflected fallback)
- **Containers** — `__len__`, `__contains__`, `__getitem__`, `__setitem__`
- **Iteration** — `__iter__` + `__next__` (`for x in obj:` calls both)
- **Callable** — `__call__` (`obj(args)` dispatches here)
- **Context manager** — `__enter__`, `__exit__` (`with obj:` works)
- **Truthiness** — `__bool__` or `__len__` (in `if obj:` / `while obj:`)
- **Stringify** — `__str__` (print/str/f-strings), `__repr__`

### Exceptions

`try`/`except`/`else`/`finally`, typed `except ValueError:`, `raise`,
bare re-raise inside a handler, `assert`

### Context managers

`with obj as x:` — calls `__enter__` and `__exit__`. Works with `io.StringIO`,
`io.BytesIO`, `open()`, and any class that defines the two methods.

### Modules

`import`, `from … import`, relative imports, inline assembly (`@assembly_func`,
`include("pkg.asmpkg")`)

### Inline assembly

```python
from asmpython.assembly import assembly_func


@assembly_func
def popcnt(x: int) -> int:
    """
    popcnt rax, rdi     ; SysV: x in rdi
    ret
    """
```

The docstring is raw NASM emitted verbatim as the function body. Arguments
arrive in the platform's integer-arg registers.

---

## Assembly class

`from asmpython.assembly import Assembly` gives a chainable builder for
generating NASM programmatically:

```python
from asmpython.assembly import Assembly

a = Assembly()
a.mov("rax", 0).xor("rbx", "rbx").label("loop").inc("rax").dec("rbx").jnz("loop").ret()
print(a.emit())
```

Supports 150+ instructions: full integer ALU, SSE/AVX, atomics, system calls,
all directives.

---

## Toolchain requirements

- **`nasm`** ≥ 2.15 on PATH — assembler
- **`gcc`** on PATH — linker driver (pulls in libc/msvcrt)
- `--target freestanding` requires only `nasm` (no linker step)

---

## Tests

```sh
python -m tests.runner
```

The harness reads `tests/cases/*.py` (positive: must compile and produce
matching stdout) and `tests/cases_fail/*.py` (negative: must fail with a
matching error substring).

```python
# expect:
# hello, world
# 42

print("hello, world")
print(42)
```

```python
# expect-error: undefined variable 'x'
print(x)
```

Input programs use `# stdin:` lines.

---

## Architecture

```text
asmpython/
├── __init__.py         package version
├── __main__.py         python -m asmpython entry
├── assembly/           @assembly_func, Assembly builder, include()
├── stdlib/             math, os, sys, time, random, io, fractions, …
├── asmlib/             hardware, network, gui bindings
└── _compiler/
    ├── lexer.py        indent-aware tokenizer
    ├── parser.py       recursive-descent parser
    ├── ast_nodes.py    AST dataclasses + expr_type()
    ├── sema.py         name resolution, type inference, import binding
    ├── codegen.py      target-agnostic code generation (~11 000 lines)
    ├── target_windows.py  PE64, MS x64 ABI
    ├── target_linux.py    ELF64, System V AMD64 ABI
    ├── target_freestanding.py  Multiboot1, bare-metal runtime
    ├── target_freestanding16.py  BIOS MBR + 16-bit bootstrap
    └── driver.py       invokes nasm + gcc
```

**Pipeline**: lex → parse → sema → codegen → nasm → gcc (one pass each).

---

## License

MIT. See [CHANGELOG.md](CHANGELOG.md) for version history and
[roadmap.md](roadmap.md) for planned releases.
