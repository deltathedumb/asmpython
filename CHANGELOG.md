# Changelog

All notable changes to asmpython are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [Unreleased]

CPython-parity expansion: making common idioms compile and produce correct
output rather than silent miscompilations.

### Added

- **Container repr for `print()` and `str()`.** `print(x)` / `str(x)` now
  render Python-style output for every built-in container instead of a raw
  pointer or compile error:
  - lists: `[1, 2, 3]`, `['a', 'b']`
  - dicts: `{'a': 1, 'b': 2}`
  - tuples: `(1, 2, 3)`, `(42,)` (trailing comma for 1-tuples), mixed kinds
  - sets: `{1, 2}` / `set()` when empty

  Backed by shared `_runtime_fmt_elem` + `_runtime_{list,dict,set}_repr`
  helpers; tuples are unrolled inline to honor per-slot element kinds.
- **`range()` as a first-class value.** `list(range(n))`, `sum(range(...))`,
  `len(range(...))`, and bare `range(...)` now work (materialized to a
  `list[int]` via `_runtime_range_list`), with 1/2/3-arg and negative-step
  forms. The `for x in range(...)` fast path is unchanged.
- **`str(container)`** stringifies lists/dicts/tuples/sets via their repr.
- **`str.format()`** with a literal format string: positional `{}`
  (auto-numbered), explicit `{0}`/`{1}` (including reuse), and escaped
  `{{`/`}}`. Previously it silently returned `0`.
- **f-string format specs** are honored: `f"{x:.2f}"`, `f"{n:05d}"`,
  `f"{n:x}"` (float `.Nf/.Ne/.Ng`; int `d/x/X/o` with width and zero-pad).
  Specs were previously stripped and ignored.
- **f-string conversions** `!r`/`!s`/`!a`: `f"{x!r}"` formats `x` via
  `repr()` (strings get quoted; a user class's `__repr__` takes priority over
  `__str__`), `!a` behaves like `!r`, and `!s` is the (already-default) `str()`
  conversion. Previously the conversion was silently dropped.
- **`@staticmethod`** methods are callable on the class
  (`ClassName.method(args)`), with no implicit receiver. `@classmethod` is
  accepted (call/dispatch work; class-state mutation through `cls` pending).
- **Class variables** (`class C: x = 5`, non-`@dataclass`) are static
  constants: read, write, and augmented assignment via `ClassName.x`.
- **`--target freestanding16`** — a raw, BIOS-bootable disk image. A 16-bit
  real-mode boot sector (ending in `0xAA55`) loads the kernel via INT 13h,
  enables A20, and switches 16 → 32 → 64-bit long mode, then runs the same
  64-bit kernel as `freestanding`. Verified booting under QEMU
  (`-drive format=raw,file=out.img`). Reuses the entire freestanding runtime.
- **`stdlib.math`** gains `trunc`, `nearbyint`, `asinh`/`acosh`/`atanh`,
  `exp2`/`expm1`/`log1p`, and the two-argument `copysign`, `remainder`,
  `fdim`, `fmax`, `fmin` — all thin bindings over C99 libm (present in
  msvcrt/ucrt on Windows).
- **`stdlib.os`** gains `fflush`, `feof`, `ftell`/`fseek`/`rewind` (file
  positioning), and `rename`.
- **`asmlib.hardware`** gains `rdrand` (hardware RNG), `io_wait`, and a new
  control/MSR group: `read_cr0`/`read_cr2`/`read_cr3`/`read_cr4`,
  `write_cr3`, `read_msr`/`write_msr`, `invlpg`, and `lidt` — building blocks
  for paging and IDT setup on `--target freestanding`.
- **`*expr` argument unpacking at call sites** (`f(*t)`, `obj.method(*t)`).
  `expr` must be a tuple of statically-known shape (a name, subscript, or
  attribute bound to a tuple literal or a `list[tuple[...]]` element); sema
  splices each slot in as its own positional argument before codegen, so no
  runtime varargs machinery is needed. Unpacking a `Call` result directly
  (`f(*g())`) isn't supported yet — assign it to a variable first.
- **`str.capitalize()`, `str.swapcase()`, `str.title()`** — new runtime
  helpers following the existing `upper`/`lower` pattern, including CPython's
  word-boundary rules for `title()` (any non-alpha character, including
  digits and apostrophes, starts a new word).
- **`str.zfill(width)`, `str.ljust/rjust/center(width, fillchar=' ')`** —
  numeric/text padding methods. `zfill` preserves a leading `+`/`-` sign when
  inserting zeros; `center` reproduces CPython's odd-padding split
  (`left = marg // 2 + (marg & width & 1)`).
- **`str.rpartition(sep)`** — like `partition`, but splits at the *last*
  occurrence of `sep`; returns `("", "", s)` when `sep` is absent (the mirror
  of `partition`'s `(s, "", "")`).
- **`str.removeprefix(p)`, `str.removesuffix(s)`, `str.casefold()`** —
  `removeprefix`/`removesuffix` strip the given affix only if present
  (otherwise return an unchanged copy); `casefold` is implemented as ASCII
  `lower`.
- **`hex(n)`, `oct(n)`, `bin(n)`** now actually convert (previously these were
  accepted by sema but produced a null string at runtime, printing `(null)`).
  Backed by a shared `_runtime_int_to_base` helper; matches CPython's
  `"0x1a"`/`"0o32"`/`"0b11010"` formatting, including the leading `-` for
  negative inputs.
- **`divmod(a, b)`** — returns the `(a // b, a % b)` tuple (int operands),
  using the same floor-division semantics as `//`/`%`. Previously undefined.
- **Bare `raise`** (re-raise the currently-active exception) inside an
  `except` block. `_runtime_exc_msg` is saved/restored around each
  `try`/`except` so a bare `raise` after the exception has been fully
  handled correctly reports `RuntimeError: No active exception to reraise`,
  matching CPython, instead of resurrecting a stale message.
- **`%` printf-style string formatting**: `"...%s/%d/%f..." % (args)` with a
  literal format string on the left and a tuple (or single value) on the
  right. Supports `%s`, `%r`, `%d/%i/%u`, `%o/%x/%X`, `%e/%E/%f/%F/%g/%G`, and
  `%%`, with flags/width/precision (`%05d`, `%-10s`, `%.2f`, etc.), lowered to
  the same concat-chain machinery as f-strings. `%r` formats via `repr()`
  (same as an f-string's `!r`).
- **`sorted()`, `list.sort()`, `min()`, `max()` now support `key=` and
  `reverse=`.** `key=` may be a lambda literal or a name bound to a lambda
  (returning `str` or `int`); `reverse=True` reverses the result in place
  after sorting. `min()`/`max()` over a single iterable now report the
  iterable's actual element type (previously always `any`) and correctly
  compare `list[str]` elements via string comparison instead of raw pointer
  values. `key=` on the variadic `min(a, b, ...)`/`max(a, b, ...)` form, and
  bare function references as `key=` (e.g. `key=len`), are rejected with a
  clear compile error rather than miscompiling.

### Fixed

- **Integer `//` and `%` now floor toward `-inf` like Python**, not toward
  zero like x86 `idiv`. Previously `-7 // 2` gave `-3` (and `-7 % 2` gave
  `-1`); now both match CPython (`-4` and `1`). The fix is a single shared
  adjustment in `_emit_binop_inline` (covers both binops and augmented
  assignment): if the truncated remainder is nonzero and its sign differs
  from the divisor's, decrement the quotient and add the divisor to the
  remainder.
- **Nested-container element types are tracked** through subscript and
  for-loop binding: `people[i]["k"]`, `for p in people: p["k"]` (list[dict]),
  `grid[i][j]` (list[list]), and tuple unpacking `for a, b in pairs`
  (list[tuple]) no longer print raw pointers.

- **`str(int)` / `str(float)` no longer alias a shared buffer.** Storing
  several conversions (e.g. `[str(x) for x in xs]`) previously made every
  element show the last value (`['3', '3', '3']`); each now gets a fresh copy.
- **Lambdas bound to a name are now callable.** `f = lambda x: x + 1; f(41)`
  and lambdas passed as arguments returned `0`; indirect calls through a
  local/global/parameter function pointer now work, and a name-bound lambda's
  call result is typed from its body (so str-returning lambdas print right).
- **`abs(float)`** returns a float again instead of printing its raw bits.
- **`time.difftime`** is now typed `float` (C's `difftime` returns a `double`
  in `xmm0`); declaring it `int` read the wrong register and produced garbage.
- **`del xs[i]`** now actually removes the element, shifting later elements
  down and shrinking the list (negative indices supported). **`del d[k]`**
  is now correctly wired up too (the dict-pop call existed but its key slot
  was never reserved, so it silently did nothing). Previously both forms
  compiled and ran without error but left the container unchanged.
- **`print()`/`str()` of nested containers** (`list[list]`, `list[dict]`,
  `dict[str, list]`, `dict[str, dict]`, one level deep) now recurse into the
  element/value repr instead of printing a raw pointer, e.g.
  `print([[1, 2], [3, 4]])` -> `[[1, 2], [3, 4]]` and
  `print({"a": [1, 2]})` -> `{'a': [1, 2]}`. `_runtime_fmt_elem` now carries
  an inner-kind nibble so it can call back into `_runtime_list_repr` /
  `_runtime_dict_repr` for container-typed elements.
- **`dict[str, T]` for `T` other than `int`** (`str`, `float`, or a nested
  container) now reprs correctly when read off a plain variable —
  `print(d)` for `d = {"a": "x"}` previously printed the raw string pointer
  as an integer because the value kind wasn't propagated onto the `Name`
  node.
- **Float values stored in dicts** (`{"a": 1.5}`, `d["a"] = 1.5`,
  `d.get("a")` / `d.get("a", 1.5)`) now round-trip the IEEE-754 bit pattern
  correctly. Previously these paths copied whatever was in `rax` (not
  `xmm0`, where float results actually live) into the dict slot, so any
  `dict[str, float]` value read back as garbage.
- **Whole-number floats print with a trailing `.0`**, matching CPython:
  `print(2.0)` -> `2.0` (was `2`), and likewise for list/dict elements and
  f-string interpolations. `sprintf`'s `%g` drops the decimal point for
  integral values; a new shared `_emit_float_repr_fixup` scans the result and
  appends `.0` unless it already contains `.`/`e`/`E` (a fraction or
  exponent) or `n`/`i`/`N`/`I` (`nan`/`inf`/`-inf`, left as-is).
- **`-0.0` now prints as `-0.0`, not `0.0`.** Unary `-` on a float negated by
  computing `0.0 - x`, but IEEE-754 `0.0 - 0.0` is `+0.0`, losing the sign.
  Negation now flips the sign bit directly (`xor` with `0x8000000000000000`).
- **`math.floor`/`math.ceil`/`math.trunc` now return `int`, matching
  CPython** (`math.trunc(3.7)` -> `3`, not `3.0`). Previously typed as
  `float` (the underlying libm functions return `double`), so `print(...)`
  the whole-number `.0` fix above would have made them mismatch CPython;
  the FFI call layer now supports an `f2i` return conversion
  (`cvttsd2si`) for libm functions whose Python-visible return type narrows
  to `int`.
- **`xs[i] = <float>` for `list[float]`** now stores the IEEE-754 bit pattern
  correctly (same `xmm0`-vs-`rax` issue as the dict fixes above). Previously
  `xs[1] = 9.5` corrupted the slot with whatever integer happened to be in
  `rax`, e.g. `[1.0, 2.0, 3.0]` became `[1.0, 4.94066e-324, 3.0]`.
- **Functions/methods with more than one `float` parameter, or a mix of
  `int`/pointer and `float` parameters, now compute correct results.**
  `def add(x: float, y: float) -> float: return x + y` called as
  `add(3.0, 4.0)` previously returned `8.0` instead of `7.0`; a class
  `__init__(self, x: float, y: float)` corrupted `self.x`/`self.y`. The
  caller side passed every argument — including floats — through the integer
  ABI registers (`rcx`/`rdx`/`r8`/`r9` or `rdi`/`rsi`/...), and the callee's
  prologue spilled them the same way, so float params never round-tripped
  through `xmm0`-`xmm3`/`xmm0`-`xmm7`. A single-float-param function "worked"
  only by accident (leftover `xmm0` state from the caller's last `movsd`
  survived the `call`). Both call sites and prologues now compute each
  argument's ABI register via a new shared `_assign_arg_regs`: Win64 assigns
  registers positionally (slot *N* is `xmmN` or the *N*th of
  `rcx,rdx,r8,r9`, depending on that argument's type), while SysV
  (Linux/freestanding) keeps separate integer (`rdi,rsi,rdx,rcx,r8,r9`) and
  float (`xmm0`-`xmm7`) counters. `_collect_locals` also now records each
  parameter's type in `local_types`, so reads of a float parameter inside the
  function body correctly use `movsd`/`xmm0` instead of `mov`/`rax`.

---

## [1.0.2] — 2026-06-12

### Added

- **Linux self-host build on Windows** — `build.py` now produces both
  `build\asmpython.exe` (Windows) and `build\asmpython-linux` (Linux ELF) in
  one run. The Linux target is compiled inside WSL using its native `nasm` and
  `gcc`. `build.bat` is now a thin wrapper that invokes `build.py`.

### Changed

- **Toolchain on Windows must be on PATH.** `asmpython.bat` no longer bundles
  or downloads dependencies; it requires `python`, `nasm`, and `gcc` to be
  available on PATH. `_download-deps.bat` now fetches w64devkit instead of the
  WinLibs MinGW bundle.

### Fixed

- **Linux executables now link under modern gcc.** The Linux link step passes
  `-no-pie`; the generated code uses absolute relocations against libc symbols,
  which gcc's default PIE mode rejects.

---

## [1.0.1-hotfix1] - 2026-06-12

### Changed

- **`build.bat`** changed to compile for both Linux and Windows in one run.

---

## [1.0.1] — 2026-06-12

### Added

- **`--keep-assembly`** compiler flag — the intermediate `.asm` file is now
  deleted after assembling by default; pass `--keep-assembly` to retain it.
  `--emit-asm` is unaffected and still keeps the file as before.

### Changed

- **`build.bat`** simplified to a single purpose: self-compile asmpython with
  itself to `build\asmpython.exe`. General compilation, `--test`, `--selfhost`,
  and `--run` modes have been removed; use `asmpython.bat` directly for those.

---

## [1.0.0] — 2026-06-12

First stable release.

### Added
- **`--target freestanding`** — Multiboot1-compatible flat binary output (`-f bin`)
  via NASM with no external linker. Boots in QEMU with
  `qemu-system-x86_64 -kernel <output.bin>`.
- **Freestanding runtime**: VGA text mode, COM1 serial output (with `\r\n`),
  bump allocator (256 KB heap), 64 KB kernel stack, 32→64-bit long-mode setup,
  identity-mapped page tables (first 16 MB, 2 MB huge pages).
- **`stdlib.sys`** — `exit`, `getpid`, `getenv`, `abort`, `version`, `maxsize`.
- **`stdlib.time`** — `time`, `sleep`, `clock`, `difftime`.
- **`stdlib.random`** — `seed`, `rand`, `RAND_MAX`.
- **`asmlib`** — new comprehensive hardware/network/GUI library package.
  - `asmlib.hardware` — bare-metal port I/O (`in_byte`/`out_byte`/`in_word`/
    `out_word`/`in_dword`/`out_dword`), MMIO, `rdtsc`, `cpuid`, `halt`,
    `disable_interrupts`/`enable_interrupts`, PIC 8259A (`pic_eoi`/`pic_mask`/
    `pic_unmask`), PIT (`pit_set_freq`), PS/2 keyboard (`keyboard_read`/
    `keyboard_poll`), VGA color/cursor helpers. All implemented as inline
    NASM in the freestanding codegen; stub-returns-0 on hosted targets.
  - `asmlib.network` — BSD socket API: `socket`, `bind`, `connect`, `listen`,
    `accept`, `close`, `send`, `recv`, `send_all`, byte-order helpers, address
    helpers, constants (`AF_INET`, `SOCK_STREAM`, `PORT_*`). Helper symbols
    (`_net_bind`, `_net_connect`, etc.) implemented inline in the hosted
    codegens (Linux SysV ABI and Windows x64 ABI).
  - `asmlib.gui` — SDL2 bindings: window, renderer, draw calls (`draw_line`,
    `fill_rect`, `draw_rect`), event pump, timing. Helper symbols
    (`_gui_poll_event`, `_gui_fill_rect`, etc.) implemented inline in hosted
    codegens via SDL_Rect stack allocation and static event-state buffers.
- **`Assembly` class** (stdlib.assembly) — 150+ x86-64 instruction builder
  methods, SSE/AVX, atomics, system calls, full directive set.
- **`pyproject.toml`** — project is now pip-installable (`pip install .`).
- **`examples/`** — curated example programs moved from root into a dedicated
  directory.
- **`docs.html`** — polished single-file reference documentation.

### Changed
- VGA `_vga_putchar` now mirrors all output to COM1 serial (with `\r\n`
  conversion on newlines) so freestanding programs are testable headlessly
  with `qemu … -serial stdio`.
- `_vga_attr` BSS variable controls the current VGA text attribute byte;
  defaults to `0x07` (light-grey on black) when zero.
- Freestanding section ordering fixed: `_load_end` label now correctly sits at
  the last byte of the flat binary (was 78-126 bytes short previously due to
  `.rodata` being laid out after `.data`).

### Fixed
- `str.split(sep, maxsplit)` now honours the `maxsplit` argument.
- `section .rodata` encounter-order in flat binary output: user string literals
  and float constants now fall inside `[load_addr, load_end_addr)` and are
  therefore loaded by the Multiboot1 loader.
