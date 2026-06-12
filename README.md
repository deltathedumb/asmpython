# asmpython

**A Pixelated Dream project.** asmpython compiles Python source to native x86-64 executables via NASM — no VM, no interpreter, no pip dependencies at runtime. Write `.py`, get `.exe` or ELF.

```sh
python -m asmpython hello.py          # compile for your host platform
./hello                               # or hello.exe on Windows
```

---

## Targets

| Target | Output | Requires |
| ------ | ------ | -------- |
| `windows` (default on Windows) | PE64 `.exe` | nasm, gcc (MinGW) |
| `linux` (default on Linux) | ELF64 | nasm, gcc |
| `freestanding` | Multiboot1 flat binary | nasm only — boots in QEMU |

The freestanding target produces a bare-metal kernel binary with no libc, no OS, and no linker step. It includes a VGA text-mode runtime, COM1 serial output, a bump allocator, and a long-mode setup stub.

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

  -o <path>          output path (default: source stem + .exe/.elf/.bin)
  --target <t>       windows | linux | freestanding  (auto-detected)
  --emit-asm         write .asm only, do not assemble
  --keep             keep intermediate .obj / .o files
  --check            front-end diagnostics only (no codegen)
  --json             machine-readable JSON diagnostics on stderr
  --use-runtime-lib  link pre-built libasmpython_rt instead of inlining helpers
  --onefile          single statically-linked binary (default)
  --onedir           exe + shared runtime library in a bundle directory
  --type executable  produce a binary (default)
  --type library     produce a shared library (.dll / .so)
```

---

## Standard library

### Built-in modules

| Module | Key symbols |
| ------ | ----------- |
| `math` | `sqrt`, `sin`, `cos`, `log`, `pow`, `pi`, `e`, … (22 functions + 5 constants) |
| `os` | `system`, `getenv`, `_exit`, `fopen`/`fgetc`/`fclose`, `access` |
| `sys` | `exit`, `getpid`, `getenv`, `abort`, `version`, `maxsize` |
| `time` | `time`, `sleep`, `clock`, `difftime` |
| `random` | `seed`, `rand`, `RAND_MAX` |

### asmlib — hardware, network, and GUI

`asmlib` provides bindings for hardware, network, and GUI that go well beyond what the C runtime offers.

```python
from asmlib import hardware, network, gui
```

#### `asmlib.hardware`

Bare-metal port I/O, MMIO, RDTSC, CPUID, halt, interrupt control, PIC 8259A, PIT, PS/2 keyboard, and VGA color/cursor helpers. On hosted targets all functions stub-return 0; on `--target freestanding` they emit real `in`/`out`/`wrmsr` instructions.

```python
from asmlib.hardware import out_byte, in_byte, halt, disable_interrupts

out_byte(0x3F8, ord('A'))   # write byte to COM1
c = in_byte(0x60)           # read PS/2 scan code
disable_interrupts()
halt()
```

#### `asmlib.network`

BSD-socket API: `socket`, `bind`, `connect`, `listen`, `accept`, `close`, `send`, `recv`, `send_all`, byte-order helpers (`htons`, `htonl`, `ntohs`, `ntohl`), `inet_addr`, `gethostname`, `errno`. Constants: `AF_INET`, `SOCK_STREAM`, `SOCK_DGRAM`, `PORT_HTTP`, `PORT_HTTPS`, `PORT_FTP`, `PORT_SSH`, `PORT_SMTP`, `INADDR_ANY`.

```python
from asmlib.network import socket, connect, send, recv, close
from asmlib.network import AF_INET, SOCK_STREAM, PORT_HTTP

fd = socket(AF_INET, SOCK_STREAM, 0)
connect(fd, "93.184.216.34", PORT_HTTP)
send(fd, "GET / HTTP/1.0\r\n\r\n", 0)
data = recv(fd, 4096)
print(data)
close(fd)
```

#### `asmlib.gui`

SDL2 bindings: window and renderer lifecycle, draw calls (`draw_point`, `draw_line`, `fill_rect`, `draw_rect`), event pump, keyboard and mouse state, timing. Constants: `INIT_VIDEO`, `WINDOW_SHOWN`, `EVENT_QUIT`, `EVENT_KEYDOWN`, `KEY_*`, `BUTTON_LEFT`, etc.

```python
from asmlib.gui import (init, create_window, create_renderer,
                        set_draw_color, clear, present,
                        fill_rect, poll_event, delay,
                        INIT_VIDEO, WINDOW_SHOWN, EVENT_QUIT)

init(INIT_VIDEO)
win = create_window("demo", 640, 480, WINDOW_SHOWN)
ren = create_renderer(win, -1, 0)
set_draw_color(ren, 30, 30, 30, 255)
clear(ren)
set_draw_color(ren, 200, 80, 80, 255)
fill_rect(ren, 100, 100, 200, 150)
present(ren)
delay(2000)
```

---

## Language features

### Types

- **`int`** — 64-bit signed
- **`float`** — IEEE-754 double; auto-promoted in mixed arithmetic; true division always returns float
- **`str`** — nul-terminated UTF-8; supports concat (`+`), repeat (`*`), comparison (`==`/`!=`), ordering (`<`/`>`/`<=`/`>=`), indexing, slicing (step supported), `in` / `not in`, and iteration
- **`bool`** / **`None`** — aliases for 1 / 0 / 0

### String methods

`upper`, `lower`, `strip` / `lstrip` / `rstrip`, `startswith`, `endswith`, `find`, `count`, `replace`, `split` (with optional `sep` and `maxsplit`), `rsplit`, `join`, `splitlines`, `partition`, `isdigit`, `isalpha`, `isspace`, `isupper`, `islower`

### Collections

- **`list`** — heap-allocated, dynamic capacity; supports `int`, `str`, `float` elements; `.append`, `.pop`, `.copy`, indexing, slicing, negative indices, iteration, comprehensions
- **`dict`** — open-addressed hashtable; supports `str` keys; `.get`, `.contains`, `.keys`, `.values`, `.items`, `.update`, iteration, comprehensions
- **`set`** — membership testing, `.add`, `frozenset`
- **Tuples** — unpacking assignment, `for k, v in pairs:`, `enumerate`, `zip`

### Control flow

`if`/`elif`/`else`, `while`, `for … in range/list/dict/str/tuple/enumerate/zip`, `break`, `continue`, `pass`, ternary expressions (`a if c else b`)

### Functions

Default arguments, `*args`, `**kwargs`, type annotations (parsed, not enforced), closures, `lambda`, decorators, first-class functions

### Classes

Single inheritance, `__init__`, instance attributes (any type), method dispatch, `super()`, `isinstance`, `hasattr`/`getattr`, `__str__`

### Exceptions

`try`/`except`/`else`/`finally`, typed `except ValueError:`, `raise`, re-raise, `assert`

### Modules

`import`, `from … import`, relative imports, inline assembly (`@assembly_func`, `include("pkg.asmpkg")`)

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

The docstring is raw NASM emitted verbatim as the function body. Arguments arrive in the platform's integer-arg registers.

---

## Assembly class

`from asmpython.assembly import Assembly` gives a chainable builder for generating NASM programmatically:

```python
from asmpython.assembly import Assembly

a = Assembly()
a.mov("rax", 0).xor("rbx", "rbx").label("loop").inc("rax").dec("rbx").jnz("loop").ret()
print(a.emit())
```

Supports 150+ instructions: full integer ALU, SSE/AVX, atomics, system calls, all directives.

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

The harness reads `tests/cases/*.py` (positive: must compile and produce matching stdout) and `tests/cases_fail/*.py` (negative: must fail with a matching error substring).

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
├── stdlib/             math, os, sys, time, random bindings
├── asmlib/             hardware, network, gui bindings
└── _compiler/
    ├── lexer.py        indent-aware tokenizer
    ├── parser.py       recursive-descent parser
    ├── ast_nodes.py    AST dataclasses + expr_type()
    ├── sema.py         name resolution, type inference, import binding
    ├── codegen.py      target-agnostic code generation
    ├── target_windows.py  PE64, MS x64 ABI
    ├── target_linux.py    ELF64, System V AMD64 ABI
    ├── target_freestanding.py  Multiboot1 flat binary, bare-metal runtime
    └── driver.py       invokes nasm + gcc
```

**Pipeline**: lex → parse → sema → codegen → nasm → gcc (one pass each).

---

## License

MIT. See [CHANGELOG.md](CHANGELOG.md) for version history.
