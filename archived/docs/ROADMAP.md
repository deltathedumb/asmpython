# asmpython Roadmap

asmpython compiles Python source directly to native x86-64 executables via NASM — no VM, no interpreter, no runtime dependencies. The project follows a version-milestone structure. Each release is a self-consistent capability slice with its own focus area.

---

## Released

### 1.0.0 — 2026-06-12 — Public launch

First public release. Core language, standard library bindings, and two targets.

**Language:** integer/float arithmetic, strings (concat, repeat, compare, index, slice, methods), f-strings, lists, dicts, sets, tuples, classes (single inheritance, `__init__`, instance attrs, methods, `super()`), exceptions (`try`/`except`/`raise`/`finally`/`else`), `with` statement, `@classmethod`/`@staticmethod`/`@property`, list/dict/set comprehensions, `*args`, default arguments, keyword arguments, `lambda`, first-class functions, decorators, inline assembly.

**Standard library:** `math` (22 functions), `os`, `sys`, `time`, `random`, `io`, `pathlib`, `json`, `struct`, `enum`, `fractions`, `contextlib`, `collections`, `statistics`, `uuid`, `argparse` (partial), and the `asmlib` suite (`network`, `gui`).

**Targets:** Windows PE64, Linux ELF64, `--target freestanding` (Multiboot1 bare-metal with VGA + serial + bump allocator), `--target freestanding16` (raw BIOS MBR image, real mode → long mode bootstrap).

**Tests:** 369 passing.

### 1.0.1 — 2026-06-12

`--keep-assembly` flag; `build.bat` simplification.

### 1.0.1-hotfix1 — 2026-06-12

Linux cross-build from Windows via WSL in a single `build.py` run.

### 1.0.2 — 2026-06-12

Linux executables now link correctly under modern gcc (`-no-pie`). Toolchain must be on PATH; `_download-deps.bat` updated to fetch w64devkit.

### 1.1.0 — 2026-06-16 — CPython parity expansion

Focus: close the gap between what asmpython compiles and what idiomatic Python actually looks like. The core theme is **operator protocol completion** — every standard dunder method now dispatches correctly — plus **stdlib depth** and **`__call__` support for callable instances**.

### 1.2.0 — 2026-06-17 — Graphics everywhere

Complete, batteries-included graphics library for all targets.

**`gui` module** — high-level SDL2 wrapper (`import gui`). `Canvas` window/renderer class with drawing primitives (line, rect, filled rect, circle, disc, filled triangle), sprite support via `Image` (load BMP → texture), `.update()` game loop, keyboard/mouse polling. Full SDL2 constant coverage: 26 letter keys, 10 digit keys, F1–F12, navigation, modifiers, all event types, mouse buttons, blend modes. 30+ named colors. Single import, no SDL2 knowledge required.

**`framebuffer` module** — software pixel rendering for bare-metal and UEFI (`import framebuffer`). `Framebuffer(addr, width, height, pitch, bpp)` with `put_pixel`, `clear`, `fill_rect`, `draw_rect`, `draw_line`, `draw_circle`, `fill_circle`, `draw_triangle`, `fill_triangle`. `rgb()`/`bgr()` color pack helpers. No OS, no SDL2, no runtime dependencies.

**SDL2 auto-linkage** — both targets detect SDL2 usage via a `needs_gui` property; `-lSDL2` added automatically only when SDL2 is actually called.

**`ffi_called` tracking** — codegen tracks which FFI c_names are actually called (not just imported). Runtime helper blocks emitted only on demand; constant-only SDL2 imports no longer force SDL2 linkage.

**`audio` module** — SDL2_mixer-backed sound and music (`import audio`): `Sound`/`Music`, auto-linked via `needs_audio`.

**Bitmap font rendering** — built-in 8×8 font wired into `framebuffer.Framebuffer.draw_char/draw_text` and `gui.Canvas.char/text`.

**Lumen** — the `gui` + `framebuffer` + `audio` ecosystem now has a name.

**Gap-filling in `gui`** — live key state (`Canvas.key_down`), relative mouse motion and capture (`mouse_dx`/`mouse_dy`/`relative_mouse`), cursor show/hide, runtime fullscreen/resize, clipboard text, and `gui.Font` (SDL2_ttf TrueType rendering via `Canvas.draw_ttf`) auto-linked via `needs_ttf`.

**Joystick/gamepad input, sprite transforms, tilemaps** — `gui.Joystick`/`gui.num_joysticks()` (SDL2 joystick API), `Canvas.blit_ex()` (rotated/flipped sprite blits via `SDL_RenderCopyEx`), `Canvas.blit_region()` (cropped sprite-sheet blits), `gui.Tilemap` (tile-grid rendering on top of `blit_region`), `framebuffer.Framebuffer.text()` alias.

**Tests:** 453/453 passing.

---

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

#### Additional language improvements — done

A broad set of language and stdlib improvements shipped alongside the core
1.1.0 milestones:

- **Generators** — `yield` inside `while` loops compiles to a heap-allocated
  state-machine coroutine; `for x in gen():` and manual `next()` calls both
  work.
- **Closures and `nonlocal`** — inner functions can capture and mutate
  enclosing-scope variables via heap-boxed cells.
- **In-place dunder dispatch** — `obj += x` calls `__iadd__` when defined;
  all in-place arithmetic and bitwise operators follow the same pattern.
- **`match` mapping patterns** — `case {"key": val}:` on a dict.
- **Tuple comprehensions** — `tuple(x for x in xs)` produces a fixed-width
  tuple; `for a, b in tuple_list` unpacks correctly.
- **Nested list comprehensions** — `[f(x) for row in matrix for x in row]`.
- **List/str augmented assignment** — `xs += ys` and `s += t` both work.
- **Subscript augmented assignment** — `xs[i] += n` and `d[k] += n`.
- **List slice assignment** — `xs[a:b] = ys` replaces a slice in-place.
- **List slice step** — `xs[::2]`, `xs[1::2]`, `xs[::-1]`.
- **List repetition** — `xs * n` and `n * xs`.
- **`print(sep=, end=)`** — keyword arguments respected.
- **`format(n, spec)`** — integer format specs `b`, `x`, `o`, `d`.
- **`dict(list_of_pairs)`** constructor.
- **Catchable `KeyError`** — `d[missing_key]` raises `KeyError` instead of
  panicking; the exception is catchable with `except KeyError:`.
- **List bounds checking** — `xs[i]` raises `IndexError` on out-of-bounds;
  catchable with `except IndexError:`.
- **`int()` raises `ValueError`** on non-numeric strings.
- **`isinstance()` for primitive types** — `isinstance(x, int)`,
  `isinstance(x, str)`, `isinstance(x, float)`.
- **`filter(None, xs)`** — filters falsy values without a predicate.
- **`list(zip(...))`** and **`list(filter(lambda, xs))`** constructors.
- **`max`/`min` variadic** — `max(a, b, c, ...)` with two or more arguments.
- **N-way `zip`** — `zip(A, B, C)` with three or more iterables.
- **`enumerate(start=N)`** — start offset supported.
- **`enumerate` in dict comprehensions** — `{i: x for i, x in enumerate(xs)}`.
- **`any`/`all` return `True`/`False`** strings instead of `1`/`0`.
- **`str.split()` with no arguments** — splits on whitespace.
- **`str.find(sub, start)`**, **`str.rfind`**, **`str.expandtabs`**.
- **String unpack** — `a, b, c = "abc"` binds each character.
- **`sum(xs, start)`** — initial-value argument.
- **`dict.get(k)` returns `None`** when the key is absent.
- **`dict` comp from `zip(A, B)`** — `{k: v for k, v in zip(keys, vals)}`.
- **`dict` of `dict`** — nested dict subscript assignment.
- **Bool return annotations** tracked through `FuncSig`; functions declared
  `-> bool` print `True`/`False` instead of `1`/`0`.
- **`is`/`is not` on floats** — comparison against `None` works correctly.
- **`@dataclass` float fields** — field default params typed correctly.
- **`**kwargs` type annotation** — `**kwargs: str` accepted in signatures.
- **Int-keyed sets** — `{1, 2, 3}`, `s.add(n)`, `n in s` all work; int keys
  are stored as their decimal string form using the existing FNV-1a backend.

#### Test count

448/448 passing (was 369 at 1.0.0).

---

## Planned
` Any listed implementations may change on release. `

### 3.14 — Compatibility overhaul: ARM and macOS

A platform-expansion release that broadens asmpython beyond x86-64 Windows/Linux.

**Versioning decided: 3.14.** A codebase survey (2026-06-17) confirmed
`codegen.py` hardcodes x86-64 mnemonics directly in ~3,461 lines of raw
f-string assembly emission, not through an abstracted instruction layer —
the existing `_arg_reg` / `_assign_arg_regs` / `emit_func_prologue` hooks
only abstract calling-convention bookkeeping (register assignment, stack
frame size), not instruction selection itself. ARM64 cannot be added as a
parallel target subclass the way Linux/Windows were; it requires
restructuring codegen around a proper IR that both x86-64 and AArch64
backends lower from. Per the criterion below, that makes this 3.14.

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
- **Dead-code elimination** ✓ — `_compiler/dce.py` implements three SSA-level
  passes (unreachable block removal, constant-branch folding, mark-and-sweep
  dead instruction elimination), iterated to fixpoint. `Value.def_` back-edge
  is now wired up in `ir_builder.py` so def-use traversal works correctly.
  Entry point: `dce.run_dce(func)`.
- **Peephole optimiser** — redundant `mov`/`push`/`pop` pairs eliminated
  over short windows; the existing text-level dead-store pass in `codegen.py`
  is the first step; SSA-level passes (above) will supersede it once the IR
  pipeline is fully wired.
- **Register allocation** — short-lived temporaries kept in registers
  instead of round-tripping through the stack; reduces memory traffic in
  tight numeric loops.
- **Type specialisation** — hot integer paths avoid the generic boxing
  overhead that the runtime incurs for `any`-typed values.

#### Bare-metal Raspberry Pi (AArch64)

- Gated entirely on the ARM64 IR/codegen work above — none of
  `target_freestanding.py` / `target_freestanding16.py`'s boot code is
  reusable (Multiboot1, VGA text buffer, real-mode BIOS `INT 13h`, A20/GDT
  setup are all x86-only protocols with no ARM equivalent).
- Pi boot model is GPU-firmware-loads-`kernel.img` + device-tree, not
  Multiboot — a new boot/hardware layer, not a port of the existing one.
- UART output in place of VGA text mode for early console.

#### Compiler extension system ✓

Extension-controlled custom syntax, built on the existing lexer/parser/AST/
sema/shared-IR pipeline — not runtime function calls, not source
preprocessing. `extend <name>` / `retract <name>` are module-scope-only,
forward-only, per-file, transactional directives that activate/deactivate a
`CompilerExtension` (declarative name/version/requires/conflicts/statement
handlers) inside a fresh per-`Parser` `ExtensionContext`. First and only
built-in extension: `constants` (`const NAME [: annotation] = value`,
name-rebinding lock, not deep immutability). See `docs/EXTENSIONS.md` for
the full design, limitations (current lexer's token-form ceiling, the
def/class-vs-const ordering asymmetry), and test coverage
(`tests/cases/45x_const_*.py`, `tests/cases_fail/const_*.py`/`extend_*.py`/
`retract_*.py`, `tests/test_extensions.py`,
`tests/test_program_isolation.py`). Backend-neutral: `ConstDecl` lowers
identically to an ordinary initialized assignment, so the IR-based x86-64/
ternary backends need no extension-specific code.

#### Android (.apk) — exploratory, not committed for 3.14

- Different deployment model from the other targets: packaging
  (Gradle/dex/AndroidManifest/`apksigner`) and JNI calling convention, not
  just a new ISA/object-format backend.
- Foundation exists: `--type library` already produces a `.so` via
  `gcc -shared`. Reaching a real APK means adding `JNIEnv*`/`jobject` ABI
  awareness, an NDK (clang+bionic) toolchain path, and ARM64 codegen
  (above) for real devices.
- Likely follow-up release after 3.14's ARM64 work lands, not part of it.

#### Scope decision: 1.3.0 vs 3.14 — resolved, see note above

ARM64 needs the IR rewrite (confirmed 2026-06-17 survey), so this is
3.14: a meaningful internal architecture change even though the
Python-level language surface is unchanged.

---

## Long-term / post-1.x

These are on the radar but not pinned to a specific release:

- **Self-compilation** — asmpython compiles asmpython. Requires: `list[instance]`, `dict[str, instance]`, `@dataclass` synthesis, `subprocess.run`, `sys.argv`. Currently 19/19 compiler files pass sema (lex+parse+sema); full self-host blocked on argparse API gap.
- **Memory management** — ~~heap objects are never freed~~ **working on Windows, inert on Linux**: a stop-the-world mark-sweep over a registry of tracked objects, each carrying a 16-byte `[meta][next]` header (`meta` = size | mark | kind<<32). `gc.collect()` returns the number of objects actually reclaimed, and collection also runs automatically every `gc.get_threshold()[0]` object allocations (default 700, CPython's gen-0 number) once armed by `gc.enable()`. The trigger sits inside `_runtime_objalloc` and fires **before** the allocation — after it, the fresh pointer would live only in a caller-saved register with nothing rooting it. Roots are the machine stack, the module-globals area, and any exact roots on the shadow stack, and none of them need cooperation from the backend: Win64 reads the stack base from the TEB at `gs:8` and derives the globals range by walking its own PE headers from the PEB at `gs:0x60`, checking both the `MZ` and `PE` signatures so a misparse yields an EMPTY range rather than a plausible wrong one. **The collector refuses to sweep unless it can see every root set**, because a missing one is not a degraded collection but a wrong one — skip the stack and everything held only by a local dies; skip the globals and everything held only by a module-level name dies. Linux has neither hook yet, so `collect()` there returns 0 and frees nothing by design. Getting the globals root set to actually work needed a LINKER fix, not a collector one: `pe_linker` started the mutable-data bucket wherever the rodata blob ended, so a module global could land at an address 7 mod 8 (measured: `0x140007247`), and a conservative scan that walks eight bytes at a time from a page-aligned base steps over it. The data bucket is now aligned to 8, which is right independently of the GC — a pointer-sized global should be naturally aligned. Element BUFFERS stay unregistered on purpose: a buffer has no identity, dies with its owner, and `realloc` moves it, which would invalidate a registry link on every `append`; the tracer reaches a list's elements through the owner's `kind` instead. Remaining: membership is a linked-list walk, so `_runtime_gc_is_object` is O(live objects) and the tag probe that now depends on it costs ~2x on `any`-heavy loops — a hash set is the fix. Refcounting for deterministic `del`/`__del__` is specified and refused, not implemented.
- **`re` module** — regular expressions. Requires an NFA/DFA engine; ~1 week of focused work.
- ~~**`asyncio`**~~ — done: `async def` / `await` compile to a state machine. sema turns each coroutine into an object whose `send(value)` resumes it and returns the awaitable it is blocked on; `asyncio.run` is a trampoline that steps an awaited coroutine in its parent's place, so nesting costs stack entries rather than native frames. `gather` runs its arguments in order. Reuses the generator state machine rather than adding a second suspension mechanism. Known gap: `sleep` does not honour a non-zero delay — single-threaded with no I/O to overlap there is nothing to yield to, so `sleep(0)` is exactly a no-op and correct but a timed sleep does not wait.
- **Windows ARM64 freestanding** — bare-metal on Windows Dev Kit / Snapdragon laptops.
- ~~**Audio**~~ — done: `audio` module (SDL2_mixer) with `Sound`/`Music`, auto-linked via `needs_audio`.
- ~~**Font rendering**~~ — done: built-in 8×8 bitmap font wired into `framebuffer.Framebuffer.draw_char/draw_text` and `gui.Canvas.char/text`; plus `gui.Font` (SDL_ttf) for smooth/anti-aliased TrueType text via `Canvas.draw_ttf`. PSF2 loading for `framebuffer` remains open if richer freestanding fonts are needed later.
- **Bare-metal PC speaker / AC97 audio** — SDL2_mixer covers hosted targets; freestanding audio output is still open.
