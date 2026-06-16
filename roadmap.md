# asmpython Roadmap

asmpython compiles Python source directly to native x86-64 executables via NASM — no VM, no interpreter, no runtime dependencies. The project follows a version-milestone structure. Each release is a self-consistent capability slice with its own focus area.

---

## Released

### 1.0.0 — 2026-06-12 — Public launch

First public release. Core language, standard library bindings, and two targets.

**Language:** integer/float arithmetic, strings (concat, repeat, compare, index, slice, methods), f-strings, lists, dicts, sets, tuples, classes (single inheritance, `__init__`, instance attrs, methods, `super()`), exceptions (`try`/`except`/`raise`/`finally`/`else`), `with` statement, `@classmethod`/`@staticmethod`/`@property`, list/dict/set comprehensions, `*args`, default arguments, keyword arguments, `lambda`, first-class functions, decorators, inline assembly.

**Standard library:** `math` (22 functions), `os`, `sys`, `time`, `random`, `io`, `pathlib`, `json`, `struct`, `enum`, `fractions`, `contextlib`, `collections`, `statistics`, `uuid`, `argparse` (partial), and the full `asmlib` suite (`hardware`, `network`, `gui`).

**Targets:** Windows PE64, Linux ELF64, `--target freestanding` (Multiboot1 bare-metal with VGA + serial + bump allocator), `--target freestanding16` (raw BIOS MBR image, real mode → long mode bootstrap).

**Tests:** 369 passing.

### 1.0.1 — 2026-06-12

`--keep-assembly` flag; `build.bat` simplification.

### 1.0.1-hotfix1 — 2026-06-12

Linux cross-build from Windows via WSL in a single `build.py` run.

### 1.0.2 — 2026-06-12

Linux executables now link correctly under modern gcc (`-no-pie`). Toolchain must be on PATH; `_download-deps.bat` updated to fetch w64devkit.

---

## In development

### 1.1.0 — CPython parity expansion

Focus: close the gap between what asmpython compiles and what idiomatic Python actually looks like. The core theme is **operator protocol completion** — every standard dunder method now dispatches correctly — plus **stdlib depth** and **`__call__` support for callable instances**.

#### Operator protocol — done

All binary arithmetic, comparison, and unary operators now dispatch to the appropriate dunder when the operand is a user class instance. The method is resolved up the inheritance chain at compile time (the same parent-chain walk used by method calls).

| Category | Dunders |
|----------|---------|
| Binary arithmetic | `__add__` / `__radd__`, `__sub__` / `__rsub__`, `__mul__` / `__rmul__`, `__truediv__` / `__rtruediv__`, `__floordiv__` / `__rfloordiv__`, `__mod__` / `__rmod__`, `__pow__` / `__rpow__`, `__matmul__` / `__rmatmul__` |
| Bitwise | `__and__` / `__rand__`, `__or__` / `__ror__`, `__xor__` / `__rxor__`, `__lshift__` / `__rlshift__`, `__rshift__` / `__rrshift__` |
| Unary | `__neg__`, `__pos__`, `__invert__` |
| Comparison | `__eq__` / `__ne__`, `__lt__` / `__le__` / `__gt__` / `__ge__` (with reflected fallback) |
| Builtins | `__abs__` (via `abs()`), `__hash__` (via `hash()`), `__bool__` (via truthiness), `__len__` (via `len()` and truthiness) |

#### Callable instances — done

`obj(args)` where `obj` is a user instance with `__call__` dispatches to the method. Sema normalises the argument list against the `__call__` signature (same default-filling and kwargs-packing as any other call); codegen loads `self` into arg register 0 and the user args into registers 1+.

#### `**kwargs` capture — done

Functions declared with `**kwargs` receive excess keyword arguments as a live `dict`. Key iteration (`for k in kwargs:`), containment (`"x" in kwargs`), and `len(kwargs)` all work. Value access returns an `any`-typed value.

#### stdlib improvements — done

- `fractions.Fraction` arithmetic (`+`, `-`, `*`, `/`, `**`, unary `-`, `abs()`) works end-to-end via dunder dispatch.
- `io.StringIO` / `BytesIO`: context manager protocol (`__enter__`/`__exit__`), `readable()`, `writable()`, `seekable()`, `io.text_open()`.
- `contextlib`: `suppress` and `nullcontext` are now real classes; `closing.__exit__` calls `.close()` correctly.

#### Custom iteration protocol — done

`for x in obj:` where `obj` is a user instance dispatches to `__iter__` / `__next__`. Sema verifies both methods exist on the class; codegen calls `__iter__` to obtain an iterator then loops `__next__` inside a setjmp frame that catches `StopIteration` (type 21) as the loop exit signal.

#### `x in obj` / `x not in obj` — done

The `in` and `not in` operators dispatch to `__contains__(container, needle)` when the right-hand side is a user class instance. Sema stamps `dunder_contains_owner` / `dunder_contains_negate` on the Compare node; codegen emits the method call with correct ABI register ordering.

#### `enumerate` in list comprehensions — done

`[i for i, x in enumerate(xs)]` is now supported. Sema recognises the two-target `enumerate(xs)` pattern and binds the index as `int`; codegen iterates the inner list by index, maintaining a counter slot, and produces a new list with the (index, element) pairs bound to the two targets.

#### Show-all-errors mode — done (default)

The compiler now collects every sema error in a file before exiting. All sema errors are reported together; parse errors still stop early. The `--one-error` flag restores the old stop-at-first-error behaviour. In `--check` mode a JSON array of all diagnostics is emitted, suitable for editor integration.

#### Selfhost gauntlet — 19/19

All 19 compiler source files pass lex → parse → sema without error. Fixes include: `@staticmethod` arity handling in `_maybe_bind_method_args`; `set{tuple}` replaced with list-based deduplication; `dict` spread keys use `A.Name(name="**")` sentinel instead of `None` for homogeneous list typing; `_parse_star_pattern` return type widened to `A.Pattern`.

#### Test count

381/381 passing (was 369 at 1.0.0).

---

## Planned

### 1.2.0 — Comprehensive graphics library

A new first-party graphics module (`asmlib.graphics` or a top-level `graphics`) that works across **all targets** — both hosted (Windows, Linux) and freestanding (bare-metal VGA/framebuffer).

Planned scope:

- **Hosted (Windows/Linux):** hardware-accelerated 2D via OpenGL or Direct2D; software fallback using the existing Win32 GDI / X11 paths. Sprites, tilemaps, drawing primitives, font rendering, input (keyboard + mouse + gamepad), audio.
- **Freestanding:** VGA mode 13h and VESA linear framebuffer support; primitives (lines, rects, circles, blits); PSF2 font rendering; no OS required.
- **Unified API:** the same Python-level `graphics.*` calls compile correctly for both target families. The compiler selects the backend based on `--target`.
- **Integration with `asmlib.gui`:** the existing `gui.Window` / software-renderer surface in `asmlib.gui` merges into or aligns with the new library.

This release makes asmpython viable for games, demos, embedded displays, and bare-metal graphical tools without any third-party dependency.

---

### 1.3.0 / 2.0.0 — Compatibility overhaul: ARM and macOS

A platform-expansion release that broadens asmpython beyond x86-64 Windows/Linux. The version number (1.3.0 vs 2.0.0) will be decided based on how much of the existing codegen needs restructuring.

#### ARM64 support

- New `--target linux-arm64` and `--target windows-arm64` targets.
- AArch64 instruction backend — a second ISA alongside the current x86-64 NASM backend.
- ARM64 ABI (AAPCS64 on Linux, Microsoft ARM64 ABI on Windows).
- The existing runtime helpers (`_runtime_dict_*`, `_runtime_str_*`, etc.)
  are ISA-agnostic logic — they will be retargeted or regenerated for AArch64.
- Freestanding ARM64: bare-metal Raspberry Pi 4 / 5 target (AArch64, no OS,
  UART output, device-tree-less boot).

#### macOS support

- `--target macos-x64` (Intel Macs) and `--target macos-arm64` (Apple Silicon).
- Mach-O object format; `ld` as the linker driver.
- macOS system call conventions and dynamic linker (`dyld`) integration.
- Code-signing: `--codesign` flag wraps the output in an ad-hoc signature so
  it runs on modern macOS without developer tools.
- `asmlib.gui` on macOS: Cocoa (AppKit) window via Objective-C runtime calls
  from assembly.

#### Performance and optimisation

This release is also the point at which the x86-64 backend gets a proper
optimisation pass:

- **Constant folding** — arithmetic on literal values reduced at sema time.
- **Peephole optimiser** — redundant `mov`/`push`/`pop` pairs and
  dead-store elimination over short windows of emitted instructions.
- **Register allocation** — short-lived temporaries kept in registers
  instead of round-tripping through the stack; reduces memory traffic in
  tight numeric loops.
- **Type specialisation** — hot integer paths avoid the generic boxing
  overhead that the runtime incurs for `any`-typed values.

#### Scope decision: 1.3.0 vs 2.0.0

If adding the ARM64 ISA backend requires restructuring codegen into a
proper IR (intermediate representation) layer that the x86-64 backend
also targets, the version will be 2.0.0 — a meaningful internal
architecture change even if the Python-level API is unchanged. If it
can be done as a parallel target alongside the existing codegen, 1.3.0.

---

## Long-term / post-1.x

These are on the radar but not pinned to a specific release:

- **Self-compilation** — asmpython compiles asmpython. Requires: `list[instance]`, `dict[str, instance]`, `@dataclass` synthesis, `subprocess.run`, `sys.argv`. Currently 12/19 compiler files pass sema.
- **Memory management** — reference counting so heap objects are freed. Currently all allocations leak (fine for short-lived scripts; not for long-running processes).
- **Exception classes** — `raise MyError(msg)` with structured data. Currently exceptions carry string messages only.
- **Generators / `yield`** — lazy iterators. Requires heap-allocated coroutine frames.
- **Multi-file projects / packages** — `import mymodule` resolving to `mymodule.py` in the project. Currently all code must be in one file (plus stdlib imports).
- **`@dataclass`** — synthesized `__init__`, `__repr__`, `__eq__` from field declarations. Parser already accepts `@dataclass`; the decorator currently does nothing.
- **`re` module** — regular expressions. Requires an NFA/DFA engine; ~1 week of focused work.
- **`asyncio`** — async/await. Requires coroutine frames (same as generators) plus an event loop.
- **Windows ARM64 freestanding** — bare-metal on Windows Dev Kit / Snapdragon laptops.
- **Performance** — constant folding, peephole optimizer, register allocation, type specialization for tight numeric loops.
