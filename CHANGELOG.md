# Changelog

All notable changes to asmpython are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [1.2.0] — 2026-06-17 — Graphics everywhere

A complete, batteries-included graphics library for both hosted (SDL2) and
freestanding (framebuffer) targets.

### Added

- **`gui` module** — high-level graphics package (`import gui`). Single import
  provides everything: `Canvas` window class, `Image` sprite class, 30+ named
  color constants (`BLACK`, `WHITE`, `RED`, …), full SDL2 init/window/renderer
  constants, and all keyboard/event/button constants. No need to touch SDL2
  directly for common cases.

  - `Canvas(title, w, h)` — hardware-accelerated SDL2 window+renderer.
    - `.color(packed, a=255)` — set draw color from 0xRRGGBB + alpha.
    - `.clear(c=0)` — fill canvas.
    - `.line(x0, y0, x1, y1)`, `.rect(x, y, w, h)`, `.filled_rect(x, y, w, h)` — drawing primitives.
    - `.circle(cx, cy, r)`, `.disc(cx, cy, r)` — Bresenham circle and filled disc.
    - `.ftriangle(x1, y1, x2, y2, x3, y3)` — scanline-rasterized filled triangle.
    - `.image(path)` — load a BMP file; returns an `Image` handle.
    - `.blit(img, x, y)` — draw an `Image` at (x, y) using full-window dest rect.
    - `.update()` — present frame and drain event queue; returns `1` while running.
    - `.poll()` — drain one event; returns event type or 0.
    - `.key()` — last keyboard scancode from current event buffer.
    - `.mouse_x()`, `.mouse_y()`, `.mouse_button()` — mouse state.
    - `.delay(ms)` — SDL_Delay wrapper.
    - `.close()` — destroy renderer + window.

  - `Image` — sprite loaded from disk; `.w()`, `.h()`, `.free()`.
  - All SDL2 init/window/renderer constants: `INIT_VIDEO`, `WINDOW_SHOWN`, `WINDOW_CENTERED`, `RENDERER_ACCELERATED`, etc.
  - Full keyboard coverage: `KEY_A`–`KEY_Z`, `KEY_0`–`KEY_9`, `KEY_F1`–`KEY_F12`, `KEY_ESCAPE`, `KEY_SPACE`, `KEY_RETURN`, `KEY_TAB`, `KEY_BACKSPACE`, `KEY_DELETE`, `KEY_INSERT`, `KEY_HOME`, `KEY_END`, `KEY_PAGEUP`, `KEY_PAGEDOWN`, arrow keys, all modifiers (`LCTRL`/`LSHIFT`/`LALT`/`RCTRL`/`RSHIFT`/`RALT`).
  - Mouse events: `EVENT_MOUSEBUTTONDOWN`, `EVENT_MOUSEBUTTONUP`, `EVENT_MOUSEMOTION`, `EVENT_MOUSEWHEEL`, `BUTTON_LEFT`, `BUTTON_RIGHT`, `BUTTON_MIDDLE`.
  - Blend modes: `BLEND_NONE`, `BLEND_ALPHA`, `BLEND_ADD`, `BLEND_MOD`; `Canvas.set_blend(mode)` and `Canvas.set_alpha(a)` for per-image alpha control.

- **`framebuffer` module** — software pixel rendering for bare-metal and UEFI
  targets (`import framebuffer`). No OS, no SDL2, no dependencies beyond
  `hardware.mmio_write32`/`mmio_write8`.

  - `Framebuffer(addr, width, height, pitch, bpp)` — wraps a linear memory-mapped
    framebuffer; supports 32 bpp and 8 bpp modes.
    - `.put_pixel(x, y, color)` — bounds-checked pixel write.
    - `.clear(color=0)` — fill entire framebuffer.
    - `.fill_rect(x, y, w, h, color)` — clipped filled rectangle.
    - `.draw_rect(x, y, w, h, color)` — outlined rectangle (4 edge lines).
    - `.draw_line(x0, y0, x1, y1, color)` — Bresenham line.
    - `.draw_circle(cx, cy, r, color)` — midpoint circle algorithm.
    - `.fill_circle(cx, cy, r, color)` — scanline filled circle.
    - `.draw_triangle(x1, y1, x2, y2, x3, y3, color)` — outlined triangle.
    - `.fill_triangle(x1, y1, x2, y2, x3, y3, color)` — scanline filled triangle.
  - `rgb(r, g, b)` — pack as 0x00RRGGBB (UEFI BGR wire format).
  - `bgr(r, g, b)` — pack as 0x00BBGGRR for RGB-byte-order screens.
  - Named colors: `BLACK`, `WHITE`, `RED`, `GREEN`, `BLUE`, `YELLOW`, `CYAN`, `MAGENTA`, `ORANGE`, `PURPLE`, `GRAY`, `DARK_GRAY`, `LIGHT_GRAY`, `PINK`, `BROWN`, `SKY`, `NAVY`, `LIME`, `TEAL`, `GOLD`, `CRIMSON`.

- **Texture/sprite support in `_gui_sdl`**: `create_texture`, `destroy_texture`, `query_texture_w/h`, `render_copy`, `set_texture_blend`, `set_texture_alpha`, `set_draw_blend` FFI bindings.

- **SDL2 auto-linkage**: both Windows and Linux targets detect SDL2 usage via a `needs_gui` property and automatically append `-lSDL2` to the link command — no manual flag needed.

- **`ffi_called` precision tracking**: codegen now tracks which FFI c_names are actually called (not just imported). Helper runtime blocks (`needs_gui`, `needs_math`, etc.) are emitted only when the corresponding functions are actually called, preventing spurious SDL2 linkage for constant-only imports.

### Tests

450/450 passing (was 448 at 1.1.0).

## [1.1.0] — 2026-06-16

CPython-parity expansion: making common idioms compile and produce correct output.

### Added

- **Multi-target `--target windows,linux`**: compile for multiple targets at once
- **`yield` in `for` loops and `if` branches**: generator transform uses loop-in-next + `_gen_body_transform`; yields work at any nesting depth in while/for generators.
- **`--onedir` implies `--use-runtime-lib`** at the `compile_source` API level, not just the CLI.
- **`io.StringIO` / `io.BytesIO` context managers**: `__enter__`/`__exit__`, `readable()`/`writable()`/`seekable()`, and `io.text_open()`.
- **`contextlib`**: `suppress` and `nullcontext` are now real classes; `closing.__exit__` calls `self.thing.close()`.
- **Ordering dunder dispatch** (`__lt__`/`__le__`/`__gt__`/`__ge__`) with reflected fallback; enables `Fraction` comparisons.
- **`fractions.Fraction` arithmetic**: `+`, `-`, `*`, `/`, `**`, `abs()`, unary `-`/`+` all work end-to-end.
- **`abs()` and `hash()` dispatch to `__abs__`/`__hash__`**; `hash(str)` uses the internal FNV-1a hasher.
- **Dunder operator dispatch** (`__add__`, `__sub__`, `__mul__`, `__neg__`, `__pos__`, `__invert__`, all `DUNDER_BINOP` entries) for binary and unary ops on user class instances.
- **`**kwargs` capture**: excess keyword args packed into `dict[str, any]`; supports `for k in kwargs`, `"x" in kwargs`, `len(kwargs)`.
- **Docs restructured into `docs/`**: `docs/index.html` + `docs/stdlib.html`; root `docs.html` redirects for backwards compatibility.
- **`@classmethod` `cls.field` access**: `cls.attr` reads/writes rewritten to `ClassName.attr` at sema time.
- **Instance truthiness via `__bool__`/`__len__`**: `if obj:` / `while obj:` / `not obj` dispatch to dunders; classes with neither remain truthy.
- **`--icon <path.ico>`** (`--target windows` only): embeds `.ico` as Windows icon resource via `windres`.
- **`asmlib.gui` window-icon bindings**: `load_bmp()`, `set_window_icon()`, `free_surface()` for Linux and Windows.
- **FFI codegen fix for >4 args on Windows**: `_gen_ffi_call` now uses `_assign_arg_regs`/shadow-space spilling; fixes `asmlib.gui` calls like `create_window`/`fill_rect`.
- **New `base64` module**: `b64encode`/`b64decode`, `urlsafe_*`, `b32encode`/`b32decode`, `b16encode`/`b16decode` (RFC 4648, `list[int]` byte convention).
- **User-defined exception classes**: `class MyError(Exception): pass` + `raise MyError("msg")` + `except MyError as e:` work end-to-end including subclass hierarchy.
- **`match`/`case` structural pattern matching (PEP 634)**: literals, captures, wildcards, or-patterns, sequence patterns with `*rest`, class patterns (`__match_args__`), as-patterns, and guards; lowered to if/elif chains in sema.
- **`with` statements**: `with expr as name: body` rewritten to `try/finally`; `__exit__` always called as `__exit__(None, None, None)`.
- **Multiple context managers**: `with a as x, b as y:` desugars to nested `with` at parse time.
- **`str.format()` named fields**: `"{name}".format(name="bob")` alongside positional and format-spec fields.
- **f-string zero-pad + grouping** (`f"{n:015,}"`): separator-aware zero-padding matching CPython via `_runtime_group_digits_zeropad`.
- **`@property` setters** (`@x.setter`): `obj.x = value` dispatches to the setter; assigning without a setter is a compile error.
- **Dict literal unpacking** `{**d1, "k": v, **d2}` (PEP 448): any number of `**other` spreads merged in source order.
- **Dict union operators** `d1 | d2` and `d1 |= d2` (PEP 584): build/merge dicts; `d2` wins on key conflicts.
- **Starred assignment** `a, *rest = xs` (PEP 3132): `*name` may appear anywhere in a tuple-assign target list.
- **`enumerate(iterable, start)`**: optional `start` argument sets the initial index.
- **Walrus operator `:=` (PEP 572)**: `target := value` binds and yields the value; binds in enclosing scope inside comprehensions.
- **Container repr for `print()`/`str()`**: lists, dicts, tuples, and sets render Python-style (`[1, 2]`, `{'a': 1}`, `(1,)`, `{1, 2}`).
- **`range()` as a first-class value**: `list(range(n))`, `sum(range(...))`, `len(range(...))` work; 1/2/3-arg and negative-step forms.
- **`str(container)`** stringifies lists/dicts/tuples/sets via their repr.
- **`str.format()` positional fields**: `{}` (auto-numbered), `{0}`/`{1}` (explicit), `{{`/`}}` escapes.
- **`str.format()` full format-spec + `!r`/`!s`/`!a` conversions**: reuses f-string machinery; full mini-language support.
- **f-string format specs**: `.Nf`/`.Ne`/`.Ng` for float; `d`/`x`/`X`/`o` with width and zero-pad for int.
- **f-string alignment/fill/width** (`[[fill]align]width`): `<`/`>`/`^` for str/int/float/bool; e.g. `f"{name:*^11}"`.
- **f-string binary spec** `b`/`#b`: `f"{n:b}"`, `f"{n:#010b}"` via `_runtime_int_to_binary`.
- **f-string grouping** `,`/`_` (PEP 378/515): `f"{1234567:,}"` → `"1,234,567"`; works with float and alignment.
- **f-string `.precision` for `str`**: `f"{name:.5}"` truncates to first N characters.
- **f-string conversions** `!r`/`!s`/`!a`: `f"{x!r}"` formats via `repr()`.
- **`@staticmethod`** methods callable as `ClassName.method(args)` with no implicit receiver.
- **Class variables** (`class C: x = 5`): static constants readable/writable via `ClassName.x`.
- **`--target freestanding16`**: BIOS-bootable raw disk image; 16-bit boot sector → 32 → 64-bit long mode via INT 13h.
- **`stdlib.math`**: `trunc`, `nearbyint`, `asinh`/`acosh`/`atanh`, `exp2`/`expm1`/`log1p`, `copysign`, `remainder`, `fdim`, `fmax`, `fmin`.
- **`stdlib.os`**: `fflush`, `feof`, `ftell`/`fseek`/`rewind`, `rename`.
- **`asmlib.hardware`**: `rdrand`, `io_wait`, `read_cr0`-`cr4`, `write_cr3`, `read_msr`/`write_msr`, `invlpg`, `lidt`.
- **`*expr` argument unpacking** at call sites (`f(*t)`, `obj.method(*t)`); sema splices tuple slots as positional args.
- **`str.capitalize()`, `str.swapcase()`, `str.title()`** with CPython's word-boundary rules.
- **`str.zfill(width)`, `str.ljust/rjust/center(width, fillchar)`** — numeric and text padding.
- **`str.rpartition(sep)`** — splits at last occurrence; returns `("", "", s)` when absent.
- **`str.removeprefix(p)`, `str.removesuffix(s)`, `str.casefold()`** — affix stripping and ASCII casefold.
- **`hex(n)`, `oct(n)`, `bin(n)`** now produce correct strings (`"0x1a"`, `"0o32"`, `"0b1010"`).
- **`divmod(a, b)`** — returns `(a // b, a % b)` tuple with floor-division semantics.
- **Bare `raise`** (re-raise) inside `except`; stale `_runtime_exc_msg` saved/restored per try/except.
- **`%` printf-style formatting**: `"fmt" % (args)` with `%s/%r/%d/%x/%f/%g/%%` and width/precision flags.
- **`sorted()`, `list.sort()`, `min()`, `max()` `key=` and `reverse=`**: `key=` accepts a lambda; `reverse=True` reverses in place.
- **`collections.OrderedDict.move_to_end()` and `.popitem()`**; fixed `OrderedDict.keys()`/`defaultdict.keys()` element type.
- **`collections.Counter` arithmetic** (`+`, `-`, `&`, `|`) with CPython's multiset drop-zero semantics.
- **New `csv` module**: `reader`/`writer_row`/`writer_rows`/`DictReader` operating on `list[str]`.
- **`asmlib.hardware.rdtsc()`, `cpuid()`, `rdrand()`** are real (ring-3) instructions on hosted targets.
- **New `uuid` module**: `UUID(hex_str)`, `uuid4()`; `.hex`, `str(u)`, `repr(u)`, `__eq__`.
- **`asmlib.hardware` console API**: `console_clear/putc/write/set_color/set_cursor/get_row/get_col`; works on freestanding (VGA) and hosted (ANSI).
- **Test coverage for `atexit`, `signal`, `subprocess`** stdlib modules (CPython-verified).

### Fixed

- **Windows link step with gcc 16+ / w64devkit**: added `-mconsole` to the Windows link command in `driver.py`; gcc 16 no longer infers the console subsystem CRT from the presence of `main`, defaulting to the GUI CRT (`crtexewin.o`) which requires `WinMain`. This unblocks `--selfhost` builds on updated toolchains.
- **Self-host: lifted-closure free-var forwarding**: comprehension loop variables (e.g. `a` in `[fix_expr(a) for a in args]`) were incorrectly included in `referenced` but not in `local_names`, causing a spurious `undefined variable` error during self-host codegen; fixed via `comp_suppressed` stack in `_find_free_vars`.
- **Self-host: transitive free-var propagation** across nested-function call chains now correctly threads free vars from the originating closure through intermediate lifted helpers.
- **Self-host: lifted-function name deduplication** across merged modules via `program.py`; avoids duplicate NASM labels when multiple source files define closures with the same lifted name.
- **Self-host: class-type widening**: reassignment to a sibling subclass instance now widens the variable type to the nearest common ancestor, preventing sema from misidentifying the method set and emitting wrong virtual calls.
- **pathlib `Path` properties** (`name`, `parent`, `suffix`, `stem`) now emit via `@property` dispatch rather than a non-existent plain getter; fixes self-host compilation of `pathlib`-using code.
- **Division/modulo by zero raises `ZeroDivisionError`** instead of CPU fault; float `/`/`//`/`%` by zero also raises instead of returning `inf`.
- **`--target freestanding` unhandled exceptions** now show a flashing red screen and warm-reboot after 5 seconds; SSE triple-fault fixed (`CR4.OSFXSR`/`CR4.OSXMMEXCPT` now set).
- **`except module.ExcClass as e:`** (dotted exception type) now parses and matches correctly.
- **Quoted forward-reference annotations** (`-> "ClassName"`, `-> "list[int]"`) now resolve to the real type.
- **`ospath.isdir`/`ospath.isfile`** were wrong on Windows (`opendir()` returns non-NULL for files); rewritten to use `os._stat` + `st_mode`.
- **Float default arguments** (`def f(x: float = 0.0):`) now parse correctly.
- **Platform-conditional constants** (`signal.SIGABRT`, etc.) now visible as module attributes.
- **`raise UserExcClass(n)` with int/float arg** no longer fails to assemble on Windows/Linux.
- **`subprocess.getstatusoutput`** now returns `tuple[int, str]` instead of a mixed list.
- **`docs.html`** repo URL corrected to `https://github.com/deltathedumb/asmpython`.
- **`-> list[tuple[T1, T2]]` annotations** now propagate per-slot element kinds through call sites.
- **`collections.Counter.most_common()`** now returns `list[tuple[str, int]]` matching CPython.
- **`for a, b in list[UserClass]`** now raises a compile error instead of segfaulting.
- **`print(0.0)` on Windows** printed `inf`; fixed by loading the inf bit pattern into a register before `cmp`.
- **`float + any` BinOp** now types as `float` instead of `any`; fixes `statistics.mean`/`variance` over unannotated lists.
- **`return <int>` from `-> float` function** now converts via `cvtsi2sd`.
- **`textwrap` functions** annotated `-> list[str]` (were `-> list`, printing raw pointers).
- **Stale `# expect:` blocks** corrected in 5 test cases (implementations were already correct).
- **Unannotated parameters** infer type from call-site arguments instead of defaulting to `int`.
- **`try`/`except` dispatches on actual exception type** including multiple clauses, type tuples, and the builtin exception hierarchy.
- **Integer `//` and `%` floor toward `-inf`** (Python semantics); `-7 // 2` now gives `-4`.
- **Nested-container element types tracked** through subscript and for-loop binding (`list[dict]`, `list[list]`, `list[tuple]`).
- **Dicts iterate in insertion order** (CPython 3.7+ semantics); new `order_buf` field in the dict/set header.
- **`str(int)`/`str(float)` no longer alias a shared buffer**; each conversion gets a fresh copy.
- **Lambdas bound to a name are callable**; indirect calls through locals/globals/parameters now work.
- **`abs(float)`** returns a float (was printing raw bits).
- **`time.difftime`** typed as `float` (reads from `xmm0`).
- **`del xs[i]`** and **`del d[k]`** now actually remove the element (were no-ops before).
- **Nested container `print()`/`str()`** recurses into element repr one level deep.
- **`dict[str, T]` for non-int `T`** reprs correctly when read off a plain variable.
- **Float values stored in dicts** now round-trip the IEEE-754 bit pattern (was reading from `rax` instead of `xmm0`).
- **Whole-number floats print with `.0`** (`print(2.0)` → `2.0`); uses `_emit_float_repr_fixup`.
- **`-0.0` prints as `-0.0`**; unary minus now XORs the sign bit instead of `0.0 - x`.
- **`math.floor`/`math.ceil`/`math.trunc` return `int`** (were `float`); FFI layer gains `f2i` return conversion.
- **`xs[i] = <float>` for `list[float]`** now stores the IEEE-754 bit pattern correctly.
- **Functions with multiple float parameters** now compute correct results; ABI registers assigned via new `_assign_arg_regs`.
- **`float **` and `**=`** now work via libm `pow(double, double)`.
- **`set.discard()`, `set.remove()`, `set.copy()`, `set.pop()`** implemented (codegen previously raised `NotImplementedError`).
- **Set literals/methods with non-str elements** raise a compile-time `SemaError` instead of segfaulting.
- **`@property` getters** work: `obj.x` on a `@property` method invokes the getter via virtual dispatch.
- **Tuple-assignment targets can be subscripts/attributes**: `xs[i], xs[j] = xs[j], xs[i]` and `self.x, self.y = self.y, self.x`.
- **`type(x)`** returns real `"<class '...'>"` string; builtin types return interned strings; `bool`/`NoneType` reported correctly.
- **`bool` and `None` print as `True`/`False`/`None`** in `print()`, `str()`, `repr()`, and f-strings.
- **`repr(x)` on user class instances** calls `__repr__`/`__str__` (was printing the heap address).
- **`a == b` / `a != b` on instances with `__eq__`** dispatches to `__eq__` (was raw pointer comparison).

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
