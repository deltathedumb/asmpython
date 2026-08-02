# asmpython

**A Pixelated Dream project.** asmpython's mission is simple: take the Python code people actually write — loops, lists, dicts, math, basic classes, the occasional `import` — and turn it into a fast native executable with the least possible fuss. No virtual machine, no interpreter, no `pip install` dance, no opinions about your build system. Write `.py`, get `.exe` (or ELF).

asmpython is built around two goals:

- **Easiest to use.** One command (`python -m asmpython foo.py`) goes from source to native binary. No project file, no toolchain manifest, no decorators or type stubs needed for things to work.
- **Compatible with what people actually write.** The supported subset deliberately tracks "what 80% of small Python programs look like": hash maps, list iteration, f-strings, math libraries, recursion, simple control flow. We don't aim for CPython parity — we aim for *your* program just working.

Under the hood, source `.py` files compile to NASM, get assembled, then linked into a native executable for **Windows (PE64)** or **Linux (ELF64)**. Strings are nul-terminated; lists and dicts are heap-allocated with stable handles so reassignment isn't required after growth. Allocations come from libc `malloc`/`realloc`; runtime support comes from libc/msvcrt.

- ~4,000 lines of Python compiler driving ~250 lines of NASM runtime per program
- One pass each: lex → parse → semantic analysis → codegen → NASM → linker
- 61 end-to-end tests (positive + negative), all passing on Windows; ELF output assembles cleanly under cross-NASM
- No dependencies beyond `nasm` and `gcc` on PATH

---

## 🐍 The release goal: 1.0 with a real standard library — by March 2027

**1.0 is the public release.** It requires two things: the first-party standard library **`asmpython.libs`**, and broad **Python compatibility (the 99.9% target)**.

The library has three pillars:

- **`asmpython.libs.os`** — OS features (files, processes, environment, time), user-mode, backed by syscalls / the C runtime.
- **`asmpython.libs.net`** — networking: sockets (bind / listen / connect / send / recv), cross-platform over Winsock and POSIX.
- **`asmpython.libs.hardware`** — **ring-0 / `--freestanding` driver-grade** hardware access: code that runs as a loadable driver (`.sys` / `.ko`) or bare-metal, with no OS or libc underneath.

The private FFI-binding registry (`asmpython/_stdlib/`) folds into `asmpython.libs` — `libs` becomes the single public stdlib surface.

Compatibility means the [roadmap](./roadmap.md)'s 99.9% goal: idiomatic Python compiles, or fails with a clear "not implemented" message — never a silent miscompile. That's a multi-year arc and the main tension with the March 2027 date; the roadmap's "Feasibility" note tracks the open product decision (keep the date and ship the self-host-grade subset, or keep 99.9% and move the date out).

The schedule driver is `hardware`. `os` and `net` are user-mode and build on the existing FFI + runtime; `hardware` at ring-0 needs a **second backend** — a `--freestanding` output mode with no libc, a custom entry point, kernel calling conventions, a non-`malloc` runtime, and (on Windows) driver signing. That backend is most of the work between here and 1.0. The `freestanding: true` flag in the [`.asmpkg` package format](asmpython/assembly/pkgformat.py) is the foundation it builds on. **March 2027 is the target; it's aggressive — it holds only if freestanding work starts soon.**

### Parallel track: asmpython compiles asmpython

**Self-compilation** — the compiler's own source compiling through asmpython and reproducing itself — is tracked independently and is **not a gate for 1.0**. Today the compiler is ~6,000 lines of CPython emitting NASM; the front-end gauntlet (`python -m selfhost.check`) measures the distance to it. Progress and the current frontier live in [roadmap.md](./roadmap.md#self-host-gap-audit-what-the-compiler-source-actually-needs).

---

## Status at a glance

| Phase | Feature | State | Notes |
| ----- | ------- | ----- | ----- |
| 1 | Lexer + parser | ✅ | Indent-aware, full operator set |
| 2 | Source-line diagnostics | ✅ | `file:line:col` with caret pointer |
| 3 | Semantic analysis | ✅ | Undefined names, arity, scope, type sanity |
| 4 | Integer arithmetic | ✅ | 64-bit signed, all standard ops |
| 5 | Control flow | ✅ | if/elif/else, while, for/range, break, continue, pass |
| 6 | Functions + recursion | ✅ | def, return, positional args; int return type only |
| 7 | Strings | ✅ | nul-terminated, immutable; concat/repeat/eq/index/slice/`in` + methods |
| 8 | F-strings | ✅ | Anywhere a `str` is expected (lowered through runtime concat) |
| 9 | Lists | ✅ | Heap-allocated, indexing, append, pop, iteration; homogeneous int / str / float |
| 10 | Dicts | ✅ | Open-addressed hashtable; str keys, int values; .get, .contains, iteration |
| 11 | Floats | ✅ | XMM regs, mixed arithmetic, full coercion |
| 12 | Imports / FFI | ✅ | `import math` / `from math import ...`; binds to libc/msvcrt |
| 13 | Test harness | ✅ | `# expect:` / `# expect-error:` / `# stdin:` blocks |
| 14 | Classes | ✅ | `__init__`, instance attributes, methods, single inheritance |
| 15 | Exceptions | ✅ | `try`/`except`/`raise` with hand-rolled setjmp/longjmp |
| 16 | Runtime library | ✅ | `--use-runtime-lib` links pre-built `libasmpython_rt_<target>.a`; 47-67% smaller `.asm` per program |

---

## What works today

### Numbers

- **Integer literals**: decimal, hex (`0x1F`), binary (`0b1010`), octal (`0o17`)
- **Underscore separators**: `1_000_000`, `0xFF_FF_FF`
- **Float literals**: `3.14`, `1.5e-3`, `.5`, `1e10`
- 64-bit signed throughout (no bignum)
- IEEE-754 doubles for floats (no `Decimal`, no half/single precision)
- Mixed int/float arithmetic auto-promotes to float
- Python's true division: `1 / 2 == 0.5` even on ints

### Strings

- Single and double quoted (`'hi'`, `"hi"`)
- Standard escapes: `\n \t \r \0 \\ \' \"`
- Nul-terminated internally, length computed on demand
- **What you can do**: `print(s)`, `len(s)`, `int(s)`, `s1 + s2` (concat), `"-" * 80` (repeat), `s == t` / `s != t` (comparison), `s[i]` (indexing), `s[i:j]` (slicing — supports omitted endpoints and negative indices, no step yet), `"sub" in s` / `"sub" not in s`, pass to FFI as `str` arg
- **Methods**: `.upper()`, `.lower()`, `.strip()` / `.lstrip()` / `.rstrip()`, `.startswith(p)`, `.endswith(s)`, `.find(needle)` (returns -1 if not found), `.count(needle)`, `.replace(old, new)` — all return fresh allocations where they need to
- **Triple-quoted strings**: `"""..."""` and `'''...'''` literals span newlines and respect the standard escape set; at module top level they act as docstrings (no-op ExprStmts).
- **Iteration**: `for ch in s:` walks the string a byte at a time, yielding fresh 1-char strs.
- **What you can't do (yet)**: lexicographic ordering with `<`/`>`, `.split()` / `.join()` (need list-of-string), slice step `s[::2]`

### F-strings

- `f"x = {x}, y = {value + 1}"`
- Nested braces escape via `{{` and `}}`
- Expression segments can be any int/float/str expression including function calls
- Legal as a direct argument to `print()` (segments emitted contiguously, no allocation) or as a value anywhere a `str` is expected — when used as a value, segments are str-converted and joined via the runtime concat helper.

### Operators (full set)

```text
+ - * / // %               arithmetic + true div + floor div + mod
== != < <= > >=            comparisons (chainable: 0 < x < 10)
and or not                 short-circuit boolean
in / not in                membership (strings only)
is / is not                identity (lowers to integer equality)
& | ^ ~ << >>              bitwise (ints only; sema rejects floats)
```

All compound assignments work: `+= -= *= /= //= %= &= |= ^= <<= >>=` (on names *and* on `self.x`-style attribute targets).

### Control flow

```python
if cond:
    ...
elif other:
    ...
else:
    ...

while cond:
    ...
    if done:
        break
    if skip:
        continue

for i in range(10):       # 1-arg
for i in range(2, 20):    # 2-arg
for i in range(20, 0, -2): # 3-arg, runtime-checked sign
for x in my_list:
for k in my_dict:         # iterates over string keys

pass
```

### Functions

```python
def fact(n):
    if n <= 1:
        return 1
    return n * fact(n - 1)
```

- Positional arguments with **default values** (int / str / True / False / None literals only; floats wait on the call-site xmm0/rax plumbing).
- Type annotations on params and return (`def f(x: int = 0) -> int:`) parse but are stripped — they don't drive inference yet.
- The `*` keyword-only separator parses but doesn't change semantics — every param is still positional.
- No `*args`, no `**kwargs`, no keyword args at call sites.
- Recursion works at any depth (limited only by OS stack)
- Implicit `return 0` if you fall off the end
- **All function arguments and return values are currently typed as `int`.** A function can compute floats or strings internally but its *signature* is int-in, int-out. (To return a float right now, you'd need to box it through a heap allocation — not implemented.)

### Tuple assignment

```python
a, b = 1, 2          # parallel binding
a, b = b, a          # swap -- right side evaluated first
prev, curr = curr, prev + curr   # fibonacci two-state
x, y, z = 10, 20, 30
```

- All RHS expressions are evaluated into temporaries before any store happens, so the swap idiom works correctly.
- LHS must be plain names (no nested unpacking, no `*rest`, no subscript / attribute targets yet).
- Values must be `int` for now — same restriction as regular variables; mixed-type tuples wait on [[boxed-values]].
- Tuples are **not** yet first-class values: there's no `(a, b)` literal you can store in a variable, return from a function, or index into. The current form is `targets = values`, that's it.

### Lists

```python
xs = [1, 2, 3]
xs[0] = 10
xs.append(99)
last = xs.pop()
print(len(xs))
for x in xs:
    print(x)
```

- Heap-allocated with a stable header pointer — variables don't need to be reassigned after growth (the buffer is what gets reallocated, header stays put)
- Open dynamic capacity, doubles on overflow
- **Elements may be `int`, `str`, or `float`** — but the list is *homogeneous*: every element of a single list must be the same kind. The element type is pinned by the first literal (`[1, 2]` → `list[int]`) or, for empty literals (`xs = []`), by the first `.append`.
- Mixed-type lists (`[1, "two", 3.0]`) still error out — they need the planned tagged-value runtime to land.
- Nested lists (`list[list[int]]`) also wait on the tagged-value runtime.
- No slicing, no negative indices, no comprehensions

### Dicts

```python
d = {"alice": 30, "bob": 25}
d["carol"] = 28
print(d["alice"])
print(len(d))
print(d.contains("dave"))
val = d.get("eve", -1)
for key in d:
    print(key, "=", d[key])
```

- Open-addressed hashtable with FNV-1a hashing and linear probing
- Stable header (32 bytes), growable slot buffer (16 bytes per slot)
- Load factor: grows when `(length + tombstones) >= capacity * 3/4`
- Keys are strdup'd on insert (the dict owns key memory)
- **Keys must be str, values must be int** in v1
- Indexing a missing key prints `KeyError: key not in dict` and exits 1 (no exception handling yet)
- No `del`, no `.keys()`/`.values()`/`.items()` (would need list-of-string materialization)

### Classes

```python
class Shape:
    def __init__(self, name):
        self.name = name

    def area(self):
        return 0

class Square(Shape):
    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side * self.side

sq = Square(5)
print(sq.area())        # 25
print(sq.side)          # 5
```

- Single inheritance with method lookup walking the parent chain
- `__init__` is optional; without one, the instance just starts with no attributes
- Methods take an explicit `self` as the first parameter
- Instance attributes are stored in a per-instance `str -> int` dict — reading an unset attribute returns `0` rather than raising
- **Static dispatch**: `obj.method()` resolves to the exact `Class.method` at compile time based on the static type of `obj`. There's no vtable, so a `Shape *` cannot dispatch to `Square.area` polymorphically — you have to call methods on the concrete type.
- **Instance attribute values must be int** in v1 (same restriction as dict values).
- No class attributes, no `@classmethod`, no `@staticmethod`, no `@property`, no `super()`, no `__slots__`, no other dunder methods (`__repr__`, `__eq__`, etc.).

### Exceptions

```python
def parse(s):
    if len(s) == 0:
        raise "empty input"
    return int(s)

try:
    n = parse("")
    print("got", n)
except as msg:
    print("error:", msg)
```

- `try: ... except [as name]: ...` — single handler, no `finally` or `else` clauses
- `raise <string>` — raises a string message (other types not supported yet)
- Stack unwinding via hand-rolled `setjmp`/`longjmp` (saves the 6 callee-saved registers + rsp + rip into a 64-byte buffer)
- `except` is bare — it catches everything (no exception classes or selective filtering yet)
- `except as e` binds the raised string to the local `e`
- Uncaught raises print `Unhandled exception: <message>` and exit with code 1
- Works across arbitrary call-stack depth: a `raise` deep inside a recursive function jumps straight to the enclosing `try`
- The handler chain is a global linked list; nested `try` blocks compose correctly
- **Memory leak warning**: longjmp skips intermediate frames without running cleanup. Since we don't have destructors yet, this only matters for allocations made between the `try` and the `raise` — they leak.

### Builtins

| Builtin | Signature | Notes |
| ------- | --------- | ----- |
| `print(*args)` | 0–64 args, mixed int/float/str | Space-separated, trailing newline |
| `len(x)` | str / list / dict → int | O(1) on list and dict; O(n) on string (strlen) |
| `int(x)` | str / float / int → int | float truncates toward zero |
| `float(x)` | str / int / float → float | |
| `str(x)` | int / float / str → str | float uses `%g` |
| `input(prompt?)` | optional str → str | Reads one line from stdin, strips `\n` |

`True`, `False`, `None` parse as literals 1, 0, 0 respectively.

### Imports / FFI

```python
import math
print(math.sqrt(2.0))
print(math.pi)

from math import sin, cos
print(sin(0.0), cos(0.0))
```

- Modules live in [asmpython/_stdlib/](asmpython/_stdlib/) as Python files that declare a `BINDINGS` dict
- Each binding is either `Func(arg_types, ret_type, c_name)` or `Const(ty, value)`
- The compiler emits `extern <c_name>` and dispatches with the correct ABI (System V on Linux, MS x64 on Windows)
- Int → float promotion happens at the call site
- **Available now**:
  - `math` — sqrt, cbrt, exp, log, log2, log10, sin, cos, tan, asin, acos, atan, sinh, cosh, tanh, floor, ceil, fabs, pow, atan2, hypot, fmod + constants pi, e, tau, inf, nan
  - `os` — system(cmd), getenv(name), _exit(code)
- **Adding a new module**: drop `asmpython/_stdlib/<name>.py` with a `BINDINGS` dict; no compiler changes needed
- **Limitation**: only int/float/str argument and return types. Anything taking structs, varargs, or callbacks needs custom plumbing.

### Inline assembly — `asmpython.assembly`

```python
from asmpython.assembly import assembly_func, include


@assembly_func
def add(a: int, b: int) -> int:
    """
    mov rax, rcx        ; Win64: args in rcx, rdx (SysV: rdi, rsi)
    add rax, rdx
    ret
    """


include("mathx")        # link mathx.asmpkg, sibling of this source file


def main():
    print(add(2, 3))    # routed into the inline NASM above
    print(square(9))    # exported by the included package
```

- **`@assembly_func`** — the decorated function's docstring is raw NASM, emitted verbatim as the function body under its symbol. The Python signature is the contract: parameter and return types let call sites type-check and pick the ABI registers. Arguments arrive in the target's integer-arg registers (rdi/rsi/… on System V, rcx/rdx/… on Win64); the body returns its result in `rax`. Works on free functions and methods.
- **`include("name")`** — pulls in an *assembly package*, a `<name>.asmpkg` directory (or single file) resolved next to the source. A package ships a `manifest.txt` (its exported symbols + signatures) and one or more `.asm` files; its NASM is concatenated into the program and its exports become callable. This is the foundation for a future `--freestanding` mode where the whole runtime is supplied as `.asmpkg` rather than linked from libc. See `asmpython/assembly/pkgformat.py` for the manifest grammar.
- Both are **compile-time directives**. Under plain CPython they stay importable (linters and type-checkers are happy): `assembly_func` returns a stub that raises if actually called, and `include` just records the request.

### Linter-only blocks — `# [compiler: ignore_start]`

Code that exists only to satisfy a linter or type-checker — a placeholder class the real program supplies in assembly, say — can be fenced off so the compiler never sees it:

```python
# [compiler: ignore_start]
class mylib:
    def placeholder(self): ...   # type stubs, never compiled
# [compiler: ignore_end]
```

The lexer blanks every line of the block (markers included) before tokenizing, so the fenced code can reference names asmpython doesn't know without producing errors. Blank-replacement keeps line/column numbers in diagnostics aligned with the original file.

### Diagnostics

Errors print with file path, line, column, and a caret pointer:

```text
examples/broken.py:2:7: semantic error: undefined variable 'oops'
  print(oops)
        ^
```

Categories: `lex error`, `parse error`, `semantic error`. Codegen never raises user-facing errors — by the time we reach it, sema has validated the program.

Negative tests verify error message substrings are stable.

---

## What does NOT work yet

These are honest gaps, listed roughly by how often they matter in real Python code:

### Numeric and string

- **String ordering** (`s1 < s2`, sorting). The strcmp runtime is wired up for `==`/`!=` but lexicographic ordering through `<`/`>` isn't exposed yet.
- **Slice step** (`s[::2]`, `s[::-1]`). Parser only accepts two-component slices `[start:stop]` for now.
- **String iteration** (`for ch in s`). Need to lift the loop machinery to know about string length.
- **String methods**: `.upper()`, `.lower()`, `.split()`, `.strip()`, `.replace()`, `.startswith()`, `.endswith()`, `.find()`, `.join()`, `.format()`.
- **Implicit string ↔ number conversions** in `print` (currently strict types per arg).
- **Integer width**: no `bytes`, no `int` larger than 64 bits, no bignum.
- **Numeric types**: no `Decimal`, no `complex`, no `Fraction`.

### Collections

- **Tuples** (`(a, b)`, multiple assignment `a, b = x, y`).
- **Sets** (`{1, 2, 3}` — clashes with dict syntax; needs disambiguation).
- **Mixed-type lists** (`[1, "two", 3.0]`) and **nested lists** (`list[list[int]]`). Homogeneous `list[int]`, `list[str]`, `list[float]` already work; nesting and mixing need a tagged-element representation.
- **Dict with int keys** or **dict with non-int values**. Needs either a typed-per-dict ABI or runtime type tags.
- **Slicing**: `xs[1:5]`, `xs[::-1]`.
- **`in` operator**: `x in lst`, `k in d`. We have `.contains` as a workaround.
- **List/dict comprehensions** (`[x*2 for x in xs]`, `{k: v for ...}`).
- **`.keys()`, `.values()`, `.items()`** on dicts.
- **`del`** on dict keys or list indices.

### Missing — functions

- **Default argument values** (`def f(x=10):`).
- **Keyword arguments** (`f(name="x")`).
- **`*args` and `**kwargs`**.
- **Closures and nested functions** — `def` only works at module scope.
- **Lambda expressions**.
- **Type annotations** are parsed for `-> name` on def headers but ignored; param types can't be annotated yet.
- **Functions returning float or str** — return type is always int. To return a float, you'd need to write the result to a heap slot, return a pointer, and have the caller read it back as float.
- **First-class functions** — can't pass a function as an argument or store one in a variable.
- **Generators / `yield`**.

### Missing — object model

- **Polymorphism / virtual dispatch** — methods resolve statically based on the variable's declared type. A parameter typed as `Shape` can't dispatch to `Square.area`.
- **Multiple inheritance**, MRO, `super()`.
- **Dunder methods** beyond `__init__` (`__repr__`, `__eq__`, `__hash__`, `__add__`, etc.).
- **`isinstance` / `type()`** — no runtime type queries.
- **Class attributes** (statics shared across instances), `@classmethod`, `@staticmethod`, `@property`.
- **Instance attribute values must be int** — strings, lists, floats, or other instances as attribute values aren't supported yet.

### Missing — control flow

- **`finally`** and **`try` / `except` / `else`** clauses — only the bare `try`/`except` two-block form is supported.
- **Exception classes** — `raise` requires a string; you can't `raise ValueError("…")` or filter handlers by type.
- **Re-raise from inside a handler** — no `raise` form that takes no argument.
- **`with`** context managers.
- **`assert`** — could be lowered to `if not <cond>: raise <msg>` but isn't.
- **`match` / `case`** statements.
- **`else` on `for` and `while`** loops.

### Modules and tooling

- **No real module loading** — `import foo` only resolves to asmpython's hardcoded `stdlib/` directory. You can't `import` another `.py` file you wrote.
- **No `if __name__ == "__main__":`** semantics — there's only one entry point per program.
- **No interactive REPL.**
- **No traceback** on runtime errors — `KeyError` from a dict miss just prints `KeyError: key not in dict` and exits 1.

### Standard library

Outside of `math` and `os`, **nothing**. Notable missing modules: `sys`, `json`, `re`, `random`, `time`, `datetime`, `collections`, `itertools`, `functools`, `pathlib`, `io`, `subprocess`, `socket`, `threading`, `asyncio`. Each would need a stdlib binding written by hand; most also need richer types (`re` needs strings with proper methods; `json` needs nested heterogeneous collections; `datetime` needs a struct type).

### Codegen quality

- **No optimization** — what you write is what you get. No constant folding, dead code elimination, register allocation, common-subexpression elimination, or inlining.
- **No tail-call optimization** — `fact(10000)` will blow the stack.
- **Stack allocation** for every local even when register-resident would suffice.
- **Runtime helpers are inlined into every program** — `_runtime_dict_set`, `_runtime_hash_string`, etc., are emitted in every `.asm`. Should be extracted into `libasmpython_rt.a` (planned).
- **No debug info** — no DWARF, no PDB, no `--debug-asm` source-line annotations.

### Targets

- **No macOS target** — codegen path doesn't exist (System V ABI matches Linux's but Mach-O has different file format and linker conventions).
- **No 32-bit, no ARM, no other architectures.**
- **No cross-compilation tested** — Linux cross-emit produces valid ELF assembly but my dev host lacks a Linux `ld` to verify end-to-end linking.

---

## Architecture

```text
asmpython/
├── __init__.py         — package metadata; public version
├── __main__.py         — `python -m asmpython ...` (delegates to _compiler)
├── assembly/
│   └── __init__.py     — public API: @assembly_func decorator, include()
├── _compiler/          — private compiler front-end
│   ├── lexer.py        — indent-aware tokenizer; emits INDENT/DEDENT
│   ├── parser.py       — recursive-descent; produces AST
│   ├── ast_nodes.py    — dataclass node types + static expr_type() resolver
│   ├── sema.py         — name resolution, arity, type sanity, import binding
│   ├── codegen.py      — target-agnostic emit; statements, expressions,
│   │                     list/dict ABI, float arithmetic, FFI dispatch,
│   │                     inline-asm functions, runtime helpers
│   ├── target_linux.py   — Linux ELF64, System V AMD64 ABI, libc bindings
│   ├── target_windows.py — Windows PE64, MS x64 ABI, msvcrt bindings
│   ├── driver.py       — invokes NASM then gcc as linker driver
│   ├── __main__.py     — argument parsing + compile_source entry
│   └── errors.py       — CompileError with source-position rendering
├── _runtime/           — pre-built native runtime archive (libasmpython_rt)
│   └── build.py        — builds the per-target static/shared runtime
└── _stdlib/
    ├── __init__.py     — Func, Const binding dataclasses
    ├── math.py         — 22 math.h functions + 5 constants
    └── os.py           — system, getenv, _exit
```

### Pipeline

1. **Lex** the source into tokens (INDENT/DEDENT generated synthetically; `(`/`[`/`{` depth tracked to suppress in-paren newlines).
2. **Parse** into an AST. Every node carries a `SourcePos` for error attribution.
3. **Semantic analysis** walks the AST: resolves names, checks function arities, propagates inferred types into `Name` / `Call` / `Attr` / etc., and binds imports against the stdlib directory.
4. **Codegen** is a single-pass tree walk that emits NASM. Locals are pre-collected and given fixed RBP-relative offsets so the frame is finalized before the prologue is written.
5. **Driver** writes the `.asm`, runs `nasm -f win64|elf64`, then runs `gcc` (which acts as a linker driver and pulls in the C runtime). Intermediate `.obj`/`.o` is removed unless `--keep`.

### Calling conventions and runtime data types

See [docs/ABI.md](docs/ABI.md) for the full, verified specification — the
`@assembly_func` inline-NASM calling convention (System V / MS x64 argument
registers, return-value convention, symbol naming) and the exact byte-level
layout of every runtime type (`list`'s 24-byte header, `dict`'s 40-byte
header — instances share the dict layout exactly). Headers are stable
across mutations; only the underlying buffer relocates, so variables don't
need reassignment after growth.

`@assembly_func` is currently `--backend legacy`-only; `docs/ABI.md`
documents the divergence and the compiler now refuses to build a program
using it under `--backend x86-64` (the default) rather than silently
discard the NASM body.

---

## Usage

```sh
# Compile for the host platform
python -m asmpython hello.py

# Cross-target
python -m asmpython hello.py --target linux        -o hello
python -m asmpython hello.py --target windows      -o hello.exe

# Stop after writing the .asm (useful for inspection)
python -m asmpython hello.py --emit-asm

# Keep intermediate .o / .obj files
python -m asmpython hello.py --keep

# Link the pre-built runtime archive instead of inlining 400 lines of helpers.
# Shrinks per-program .asm by 50-70%, faster NASM passes.
python -m asmpython hello.py --use-runtime-lib

# Build the runtime archive ahead of time (auto-built on first use):
python -m asmpython._runtime.build --all          # both targets

# Bundling modes -- mutually exclusive.
python -m asmpython hello.py --onefile  # default: single statically-linked .exe
python -m asmpython hello.py --onedir   # exe + lib/libasmpython_rt_<target>.{dll,so}
# Short forms: -of and -od respectively.

# Output kind: an executable (default) or a shared library.
python -m asmpython mod.py --type executable        # default
python -m asmpython mod.py --type library -o mod.dll # shared lib (.dll / .so)

# Target / architecture. 'freestanding' = bare-metal, no OS/libc (backend WIP).
python -m asmpython hello.py --target windows
python -m asmpython hello.py --target freestanding  # errors until the backend lands
```

`--onedir` produces a bundle **directory** (the `-o` path is treated as the folder; default = the app name) laid out as:

```text
<bundle>/                         <bundle>/
  myapp.exe       (Windows)         myapp.elf      (Linux)
  resources/                        .resources/    (hidden on Linux)
    libasmpython_rt_win.dll           libasmpython_rt_linux.so
```

The shared runtime lives in `resources/` (`.resources/` on Linux). Linux builds add `-Wl,-rpath,$ORIGIN/.resources` so the loader finds it next to the `.elf`; on Windows a side-by-side copy of the DLL is also dropped next to the `.exe` because the Windows loader doesn't search the subfolder. Once cross-file user-module imports compile to their own `.dll`/`.so`, they land in that same resources folder.

Errors print to stderr with non-zero exit; success is silent except for the `wrote …` progress lines from each toolchain step.

### Toolchain requirements

- **`nasm`** on PATH — assembler
- **`gcc`** on PATH — used as a linker driver to pull in libc/msvcrt and provide the C runtime startup that calls `main`

The generated assembly externally references: `printf`, `sprintf`, `fputs`, `fputc`, `putchar`, `puts`, `fgets`, `strlen`, `strcmp`, `strdup`/`_strdup`, `atoll`/`_atoi64`, `atof`, `malloc`, `realloc`, `free`, `memset`, `exit`, `fmod`, `__acrt_iob_func` (Windows) / `stdin` (Linux), plus whatever the imported `math` module pulls in.

### Tests

```sh
python -m tests.runner
```

The harness scans `tests/cases/*.py` (must compile and run with matching stdout) and `tests/cases_fail/*.py` (must fail compilation with a matching error substring). Each file declares its expectations in a leading comment block:

```python
# expect:
# hello, world
# 42

print("hello, world")
print(42)
```

Or for negative tests:

```python
# expect-error: undefined variable 'x'
print(x)
```

Programs that read input declare it with `# stdin:` — lines are joined with `\n` and piped into the compiled binary.

Current count: **48 positive cases + 13 negative cases = 61 tests**, all passing on Windows.

---

## Roadmap

See [roadmap.md](./roadmap.md) for the comprehensive plan toward 99.9% Python compatibility — 12 tiers, dependency graph, effort estimates, and the open questions that need answers before each tier.

The short version, in rough priority order:

1. **First-class tuples** (`t = (a, b)`, indexing, `for k, v in pairs:`) — once tuples can be values, dict `.items()` falls out of the same machinery.
2. **String iteration** (`for ch in s`) — extend the `for` lowering to walk a string by index.
3. **`.split()` / `.join()` on strings** — gated on supporting `list[str]`.
4. **Polymorphism for classes** — a vtable per class so a `Shape` parameter can dispatch to `Square.area` at runtime. Currently dispatch is static.
5. **Mixed-type instance attributes / collection values** — let `self.name = "alice"`, `list[str]`, `dict[str, str]` work. Requires either runtime type tags or per-collection element types.
6. **Exception classes** — `raise ValueError("msg")` and `except SpecificError:` dispatch. Builds on the existing try/except plumbing.
7. **Runtime extraction**: ship the ~400 lines of runtime helpers as `libasmpython_rt.a` instead of inlining into every program. Smaller `.asm` outputs, faster builds. ~1 day.
8. **More stdlib bindings**: `sys.argv`/`sys.exit`, `time.time`/`time.sleep`, `random.random`/`random.randint`. Each ~30 minutes once the right C function is identified.
9. **macOS target** — Mach-O 64. Codegen is mostly the same as Linux (System V ABI); the differences are file format and linker invocation. Can't test from a Windows host.
10. **A real optimizer pass** — at minimum: constant folding, dead-store elimination, peephole. Maybe 1-2 weeks for something meaningful.

What's intentionally **not** on the roadmap:

- CPython parity. asmpython is a compiled language wearing Python's syntax, not a Python implementation.
- Bignum / arbitrary precision integers — would require boxing every int, defeating the speed advantage.
- The GIL, asyncio, generators, descriptors, metaclasses, the import hook system — these are interpreter features that don't fit the "compile to flat machine code" model.
- A garbage collector. Memory currently leaks (lists and dicts allocate without ever freeing). Plan is either reference counting once we have a uniform value representation, or simple arena allocation for short-lived programs.
