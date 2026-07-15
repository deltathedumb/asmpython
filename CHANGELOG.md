# Changelog

All notable changes to asmpython are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [2.0.0-preview] — in progress — Win64 ABI fixes, stdlib depth, SSA optimisation

Versioned 2.0.0 (not 1.3.0): the ARM64/macOS platform work planned for this
release needs codegen restructured around an IR layer rather than a parallel
target subclass — see `roadmap.md` for the full reasoning. Selfhosting
(asmpython compiling itself) is a stretch goal for this release, not part of
the committed scope; the platform/optimization roadmap is the actual
deliverable.

### Added

- **IR backend multi-argument `print`** (`ir_lower.py`) — the SSA lowering
  path now supports Python's default space-separated output for multiple
  `int`, `str`, and `float` arguments. Arguments are evaluated left-to-right
  before output and retained in local slots across `printf` calls. The
  x86-64 backend now also sets the SysV AMD64 variadic vector-register count,
  so float output works on both Windows and Linux targets.

- **IR backend float lists** (`ir_lower.py`, x86-64 backend) — list literals,
  reads/writes, `append`, `pop`, and list loops preserve IEEE-754 payloads
  through the existing integer-ABI list helpers via explicit IR bitcasts.

- **IR backend float dictionaries** (`ir_lower.py`) — dict literals,
  subscripts, assignment, and `get()` preserve float values through the
  dict runtime's word-sized ABI without numeric conversion.

- **IR backend class construction and direct dispatch** (`ir_lower.py`) —
  class methods now lower to their mangled symbols, constructors pass the
  allocated instance to `__init__`, and inherited constructors/methods resolve
  through the class parent chain. Mixed integer/float arithmetic and
  comparisons now emit explicit integer-to-float promotion.

- **Pyinbin source bundles** (`asmpython pyinbin package`) — projects can now
  package explicit `pyinbin_imports` module roots into a deterministic source
  tree with a qualified-module manifest and SHA-256 integrity records. Native
  runtime loading remains gated on the interpreter/VM implementation.

- **Pyinbin fallback execution** — iteration, tuples, sets, subscripting,
  augmented assignment, power, identity, membership, and richer comparisons
  now run through the bootstrap VM. Native code-generation `NotImplementedError`
  attempts pyinbin before reporting a combined failure.
- Projects declaring `pyinbin_imports` now package and execute their entry via
  pyinbin instead of being rejected before build.
- Added bootstrap pyinbin classes, instance attributes, bound methods, and
  inherited constructors.
- Added pyinbin `raise`, typed `try/except`, exception matching, and the
  bootstrap exception builtins.
- Added package context and relative `from .module import name` execution to
  the pyinbin loader.
- Expanded pyinbin calls with defaults, positional-only parameters, varargs,
  keyword-only parameters, `**kwargs`, starred arguments, decorators, chained
  comparisons/assignments, tuple unpacking, f-strings, boolean short-circuit,
  and delete statements.
- Added pyinbin bitwise/shift operators and `global` statement semantics.
- Added the CPython official `Lib/test` conformance harness and a required
  release gate for 2.0.0 readiness.

- **27 new stdlib modules** — full implementations of `token`, `tokenize`,
  `shelve` (pickle-backed, typed getters/setters for complex object persistence),
  `codecs`, `fileinput`, `linecache`, `mimetypes`, `socketserver`, `smtplib`,
  `ftplib`, `poplib`, `imaplib`, `http.server` (`http_server`),
  `xml.etree.ElementTree` (`xml_etree`), `html.parser` (`html_parser`),
  `tarfile`, `concurrent.futures` (`concurrent_futures`), `profile`, `pstats`,
  `tracemalloc`, `uu`, `quopri`, `zlib`, `ssl`, `sqlite3`, `asyncio`,
  `importlib`. All 454 tests still pass.

- **Dead-code elimination (DCE) pass** (`_compiler/dce.py`) — three-pass
  optimizer on the SSA IR, iterating to fixpoint:
  - *Unreachable block elimination*: BFS from the entry block; blocks not
    reachable via the CFG are removed and their predecessors' lists and PHI
    incoming values are updated.
  - *Constant-branch folding*: a `CONDBR` whose condition is a compile-time
    `CONST` is replaced with an unconditional `BR` to the taken arm; the
    not-taken block's predecessor list and PHI incoming values are patched
    accordingly.
  - *Dead instruction elimination*: mark-and-sweep from side-effecting
    instructions (`STORE`, `CALL`, `BR`, `CONDBR`, `RET`, `RAW_ASM`);
    any instruction whose result has no live consumer is dropped. PHI nodes
    are treated as ordinary data-flow nodes and swept away when unused.
  Entry point: `dce.run_dce(func)`.

- **`Value.def_` back-pointer now wired up** (`_compiler/ir_builder.py`) —
  `BlockBuilder._emit` and `BlockBuilder.phi` now set `result.def_` to the
  newly created `Instr`, making the SSA def-use graph fully navigable.
  Previously `def_` was always `None` for non-parameter values, which would
  have broken any analysis that walks the def-use chain (including DCE).

### Fixed

- **Collected semantic errors crashed before formatting** (`sema.py`): a
  stale debug print accessed the removed `SemaError.msg` attribute, masking
  every diagnostic when show-all-errors mode was active. Collection now uses
  the real exception object and reports all errors normally.

- **Win64 shadow-space violations**: every hand-rolled runtime helper and
  function prologue on the Windows target needs at least 32 bytes of shadow
  space below `rsp` before calling any external (libc/Win32) function, per
  the Win64 ABI. Several runtime helpers (`_runtime_chr`, `_runtime_zalloc`,
  `_runtime_dict_get`/`_dict_get_default`/`_dict_contains`,
  `_runtime_list_reverse`, `_runtime_str_strip`/`_str_splitlines`,
  `_runtime_input`, `_math_ldexp`, `_random_random`, `_gui_load_bmp`,
  `_gui_joystick_axis`/`_joystick_button`, `_audio_load_wav`,
  `_threading_lock_acquire`/`_lock_release`/`_lock_destroy`,
  `_time_sleep_ms`) allocated only 16–32 bytes, corrupting the caller's
  frame on the C call. Fixed by raising each to a 48-byte minimum.
  `emit_func_prologue`/`emit_entry_prologue` (`target_windows.py`) now also
  enforce a 48-byte frame floor for every compiled function, not just the
  hand-written helpers, so a function with few/no locals still has room for
  a callee's shadow space.

- **`@dataclass` synthesized `__init__` ignored `field(default_factory=...)`**
  (`sema.py`): when a `@dataclass` had no explicit `__init__`, the
  synthesized constructor substituted the literal integer `0` for any field
  declared `= field(default_factory=dict)` (or `list`/`set`), regardless of
  the requested factory. Every such field silently became `0` instead of a
  fresh empty container, segfaulting on first use (e.g. `self.types.update(...)`
  with `self.types == 0`). Fixed to emit `A.DictLit`/`A.ListLit`/`A.SetLit`
  literal nodes matching the requested factory.

- **Shared AST node across call sites for omitted-argument defaults**
  (`sema.py` `_bind_args`): when a call omitted an argument with a default,
  the *same* default AST node object was reused for every call site that
  omitted it. Codegen keys per-literal scratch frame slots off the node's
  `id()` (e.g. `_gen_dict_lit`'s `__dictlit_{id(e)}`), so two omitted-arg
  call sites in the same function collided on one frame slot. Fixed with
  `copy.deepcopy` per call site.

- **NULL-pointer crash in `str`/container truthiness checks** (`codegen.py`):
  truthiness tests for `str` (`if x:`, `not x`, `bool(x)`) read the first
  byte of the string pointer to check for empty-string falsiness, and
  container truthiness (`list`/`tuple`/`dict`/`set`) read the length field
  at `[ptr+8]` — both assumed the pointer is never NULL. An `Optional[str]`
  or `Optional[list/dict/...]` holding `None` is a NULL pointer, and `None`
  is also falsy, so all three call sites now test the pointer for NULL
  before dereferencing it.

- **Float binop/compare spilled the left operand across an `rsp`-touching
  right operand** (`codegen.py` `_gen_binop_float`, `_gen_compare`): both
  spilled xmm0 (the left operand) with a raw `sub rsp, 8` / `movsd [rsp],
  xmm0` while evaluating the right operand. If the right operand was, or
  contained, an FFI call (e.g. `math.pow`) — which itself adjusts `rsp` for
  Win64 shadow space / stack-passed arguments — `[rsp]` no longer pointed
  at the spilled value once the call returned, silently reading whatever
  garbage the call had left on the stack instead. Confirmed with
  `f0 + (1.0 - f0) * math.pow(1.0 - ct, 5.0)`: the `math.pow` call itself
  returned the correct value, but the surrounding `+`/`*` read back garbage
  for the left operand, producing ~75000 instead of ~0.04. Fixed by
  spilling to a stable `rbp`-relative scratch slot (keyed by the `BinOp`/
  `Compare` node's `id()`, reserved in `_collect_locals`) instead of the
  stack pointer, matching every other "park a value across a
  call-containing evaluation" scratch slot in this file (`__binstr_`,
  `__listcat_`, `__ffi_arg_`, ...).

- **`@handle.imported` dynamic calls (`_gen_dynamic_call`) skipped Win64
  shadow-space reservation for 1-4 argument calls** (`codegen.py`): the
  indirect `call rax` through a `GetProcAddress`/`SDL_GL_GetProcAddress`-
  resolved function pointer only reserved the mandatory 32-byte shadow
  space when the call had 5+ arguments (i.e. needed stack-passed args
  anyway); a 1-4 argument call left `stack_positions` empty and skipped
  `sub rsp` entirely, violating the Win64 ABI requirement that *every*
  call reserve shadow space regardless of argument count. The callee
  (any real Windows DLL function, confirmed with `glGetString` resolved
  via `SDL_GL_GetProcAddress`) then used that unreserved stack space as
  its own scratch area, silently corrupting the caller's frame — no
  crash, no error, just a wrong/garbage result with no signal that
  anything had gone wrong. Fixed by always reserving shadow space before
  the indirect call.

- **`@handle.imported` dynamic calls sign-extended every `-> int` return,
  corrupting genuine pointer returns** (`codegen.py` `_gen_dynamic_call`):
  unlike `_gen_ffi_call` (which has an explicit `ret_conv="ptr"` opt-out),
  every dynamically-resolved call with an `int`-annotated return got
  `movsxd rax, eax` — correct for a real 32-bit `int`/`GLenum` return, but
  silently truncating-then-sign-extending any function that actually
  returns a 64-bit pointer through an `int`-typed stub (there's no way to
  introspect the real C signature for a `GetProcAddress`/dlsym-resolved
  function, so the stub's annotation is a guess). Confirmed with
  `glGetString`, whose real return type is `const GLubyte*`. Fixed by
  leaving `rax` untouched after the call: correct for a real pointer, and
  still correct for a real 32-bit return since x86-64 callees compiled by
  both MSVC and GCC already zero-extend `eax` into `rax` via the
  architecture's implicit upper-32-bit zeroing on a 32-bit `mov`/`ret`.

- **`@handle.imported` dynamic calls wrote stack-spilled arguments (5th+
  parameter) before resolving the function pointer, letting the resolver's
  own call clobber them** (`codegen.py` `_gen_dynamic_call`): the stack-arg
  write loop ran inside the already-`sub rsp`-adjusted call frame, then
  `call _runtime_dict_get_default` (the pointer lookup) executed using
  that *same* frame for its own Win64 shadow space — silently overwriting
  the stack-passed arguments at `[rsp+32..)` before the real indirect call
  ever ran. Confirmed with `glReadPixels` (7 args: 4 register-passed, 3
  stack-spilled including the output buffer pointer) — the call reached
  the correct, real `glReadPixels`, but always read back zeroed/garbage
  data regardless of input, while the simpler 2-arg `glGetIntegerv` (no
  stack spill) worked correctly. Fixed by resolving the function pointer
  *before* reserving the call's own stack frame / writing stack args, the
  same ordering `_gen_import_binary`'s original comment already intended
  but `_gen_dynamic_call`'s stack-spill path didn't follow.

- **`gl_import()` builtin added** (`sema.py`, `codegen.py`,
  `target_windows.py`, `target_linux.py`): like `import_binary(path)`, but
  resolves every `@<handle>.imported` function via `SDL_GL_GetProcAddress`
  instead of `LoadLibrary`+`GetProcAddress`/`dlsym` — required for OpenGL
  functions beyond GL 1.1, which aren't statically exported by
  `opengl32.dll`/`libGL.so` and must be resolved against whichever GL
  context is current instead. Takes no arguments (unlike `import_binary`,
  there's no library path — resolution always targets the current
  context). Reuses the exact same `@handle.imported`/`imported_funcs`
  machinery as `import_binary`, just with a different resolver
  (`_emit_get_gl_proc_addr`, new per-target) and no library-handle step.
  Verified end-to-end against a real GPU (driver-reported
  `GL_VERSION`/`GL_VENDOR`/`GL_RENDERER` resolve and read back correctly;
  a real GLSL shader pair compiles, links, and renders an actual
  hardware-rasterized triangle — see `pugtk`'s `gl_triangle.py`) —
  exercised all three shadow-space/truncation/stack-spill fixes above,
  each first surfaced by this path.

- **`gl_import()` calls silently corrupted every `GLfloat` argument/
  return value** (`codegen.py` `_gen_dynamic_call`): asmpython's `float`
  type is always a 64-bit C `double` — there is no 32-bit `float` type
  anywhere in the language. OpenGL's API is `GLfloat` (32-bit) throughout
  (`glClearColor`, `glUniform*f`, ...; only a handful of legacy/fixed-
  function entry points like `glDepthRange` genuinely take `GLdouble`).
  Loading a double's raw 64 bits into an xmm register and calling a
  function that reads it as a 32-bit float reads garbage: a double `1.0`'s
  low 32 bits happen to decode as float `0.0`, which is exactly why
  `glClearColor(1.0, 0.0, 0.0, 1.0)` visibly did nothing — every channel
  silently became `0.0`. Confirmed via direct disassembly comparison
  against a hand-written C program calling the identical
  `SDL_GL_GetProcAddress`-resolved function pointer: both produced
  byte-identical machine code for the call site itself, and a minimal
  hand-assembled repro linked into the *working* C program reproduced the
  exact same silent-zero bug — conclusively isolating the issue to the
  double/float width mismatch rather than the call/resolution mechanism
  (which is correct, as the three shadow-space/truncation/stack-spill
  fixes above already demonstrated by fixing *other* real bugs along the
  way). Fixed by narrowing every `float` argument to a 32-bit single
  (`cvtsd2ss`) before loading it into the call's xmm register, and
  widening a `float`-typed return value back to a double (`cvtss2sd`)
  after the call — but only for handles created via `gl_import()`
  specifically (tracked in the new `self.gl_import_handles`), and only
  for functions not in the new `_GL_DOUBLE_FUNCS` allowlist (`glDepthRange`,
  `glClearDepth`, and the small set of other genuine `GLdouble` legacy
  entry points) — `import_binary()`-loaded libraries and those few GL
  functions keep using real doubles, since narrowing them would
  reintroduce the same corruption in the opposite direction. Verified:
  `glClearColor`/`glUniform3f` now read back exactly the requested values
  via `glGetFloatv`, `glDepthRange` still reads back correctly via
  `glGetDoublev`, and `gl_triangle.py` now renders the correct dark
  background and orange triangle (previously: blue background, black
  triangle) — screenshot-confirmed.

- **`@handle.imported` now works on class methods, not just top-level
  functions** (`codegen.py` `_gen_method_call`/`_gen_dynamic_call`): a
  class can now wrap a set of dynamically-resolved bindings (e.g. GL
  functions) behind its own constructor/API instead of forcing every
  caller to hand-declare the same top-level `@handle.imported` stubs
  before using it — `pugtk`'s `GLRenderer3D` is the motivating case
  (~20 GL bindings encapsulated as methods, giving callers a plain
  `GLRenderer3D(window, camera)` constructor). The decorator's handle
  (`@<handle>.imported`) still has to be a name resolvable where Python
  evaluates class decorators — a module-level `glfns = gl_import()`, since
  decorators run once at class-definition time, not per-instance.
  Implementation: `imported_funcs` now also scans `mod.classes[*].methods`
  (previously `mod.funcs` only), and a new reverse index
  (`imported_method_handle: (class_name, method_name) -> handle_name`)
  lets `_gen_method_call` recognize `some_instance.glClearColor(...)` as a
  dynamic dispatch through `glfns` even though `some_instance` (the
  receiver) has nothing syntactically to do with the handle. `_gen_dynamic_call`
  gained `handle_name`/`skip_self` parameters: the handle dict is now
  fetched by name (a synthesized `A.Name` lookup) instead of always
  evaluating the call's receiver expression, and parameter-type lookups
  are offset by 1 to skip a method's implicit `self` slot, which has no
  corresponding entry in the call site's real argument list. Verified
  end-to-end against real GL state (`glClearColor`/`glReadPixels` called
  as methods on a wrapper class, confirmed via pixel readback) on top of
  the float-narrowing fix above.

- **Whole-program bundler missed globals a merged class depends on**
  (`program.py` `load_program`/`_materialize_value_imports`): a module-level
  value (e.g. `glfns = gl_import()`) referenced only from inside a class's
  methods — never via an explicit `from module import glfns` — was never
  pulled into the entry module, so codegen treated it as an undefined
  variable. The existing materialization pass only walked free names inside
  directly-imported *functions*; it never looked inside merged *classes* at
  all. Fixed by tracking `class_origin: dict[class_name -> source module]`
  as classes are merged, and adding a `_class_free_names(cls)` walk (free
  names across every method body) that feeds the same resolve/materialize
  loop functions already used. Also surfaced a second gap: `gl_import` and
  `import_binary` themselves were never in `_ALWAYS_AVAILABLE`, so even once
  `glfns` was found as a free name, resolving *its own* initializer's call
  target failed the same way — both builtins are now always-available.

- **`gl_import()`-created handles resolved too early to ever work when
  bundled** (`codegen.py` `_gen_gl_import`/`_gen_dynamic_call`): once the
  bundler fix above started materializing `glfns = gl_import()` correctly,
  it surfaced a deeper ordering problem — materialized globals are
  prepended to the very top of the entry module's body, so `gl_import()`'s
  eager resolution loop (`SDL_GL_GetProcAddress` for every `@imported` stub,
  done once at the assignment) ran *before* the user's own GL context
  creation code, meaning every pointer resolved to `NULL`. This isn't a
  simple ordering bug to patch around — there's no single correct place to
  put the materialized assignment relative to arbitrary user setup code.
  Fixed architecturally: `gl_import()` now just allocates an empty handle
  dict (`_gen_gl_import` no longer resolves anything), and resolution
  happens lazily on first real use — `_gen_dynamic_call`'s GL path checks
  the handle dict first, and only calls `SDL_GL_GetProcAddress` (caching the
  result back into the dict) if the entry is still missing. Non-GL handles
  (`import_binary()`) are unaffected — they still resolve at the call site
  the same way as before, just sharing the now-slightly-refactored lookup
  code path. Verified via a class wrapping `glClearColor`/`glClear`/
  `glReadPixels`/`glGetError`, called well after context creation, with a
  pixel readback confirming the resolved pointers are real and correct.

- **Added `gl_resolve(handle, "name")` builtin** (`sema.py`, `codegen.py`):
  a side effect of the laziness fix above is that `getattr(handle, "name", 0)`
  — previously the documented way to fetch an `@imported` function's raw
  pointer for manual use (e.g. passing `glShaderSource`'s pointer through to
  `lumen.gl.shader_source`, which needs it directly rather than calling it
  as a marshalled `@imported` stub) — silently breaks: `getattr` reads the
  handle dict directly, bypassing the lazy-resolve-and-cache logic that now
  only lives inside `_gen_dynamic_call`'s actual-call path. Rather than
  special-casing `getattr` to know about GL laziness, added a dedicated
  builtin with the same resolve-or-fetch-and-cache behavior as a real call,
  minus actually invoking the function: `gl_resolve(glfns, "glShaderSource")`
  returns the pointer, resolving and caching it on first use exactly like
  calling `glfns.glShaderSource(...)` would. Sema requires the second
  argument be a string literal (the same constraint `@imported` stub names
  already have). Verified: `gl_resolve(glfns, "glShaderSource")` returns a
  nonzero pointer that successfully drives a real shader compilation via
  `lumen.gl.shader_source()`.

### Known issues

- The selfhost binary (asmpython compiling itself) still segfaults on a
  distinct, not-yet-isolated bug beyond the fixes above. Tracked as a
  follow-up; not a blocker for this release's actual scope (ARM64/macOS
  planning, see `roadmap.md`).

- **`parser.py` mutual-recursion sema error**: `_collect_refs_expr` called
  `_collect_refs` (defined later in the same scope), which asmpython's sema
  couldn't forward-resolve. Inlined `_collect_refs` (a one-liner `for`-loop)
  at every call site and deleted the function.

- **Lifted (nested) functions from imported modules silently dropped**:
  `_dedupe_lifted_funcs` was adding each module's lifted-function names to
  the shared `taken_names`/`func_names` set before the merge loop ran, so
  the subsequent `if f.name not in func_names` check always evaluated false
  for lifted funcs — present in `module.funcs` but never appended to
  `entry.funcs`. Fixed with a module-local `local_names` set for
  within-module collision detection; `taken_names` is no longer mutated.


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

- **`audio` module** — SDL2_mixer-backed sound and music (`import audio`).
  - `init(freq=44100, channels=2, chunk=2048)` / `quit()` — open/close the mixer.
  - `Sound(path)` — loads a WAV; `.play(loops=0)`, `.play_on(channel, loops=0)`, `.stop()`, `.volume(vol)`, `.playing(channel=-1)`, `.free()`.
  - `Music(path)` — streamed background music; `.play(loops=-1)`, `.stop()`, `.volume(vol)`, `.playing()`, `.free()`.
  - Constants: `MAX_VOL`, `DEFAULT_FREQ`, `DEFAULT_FORMAT`, `DEFAULT_CHANNELS`.
  - `-lSDL2_mixer` is auto-linked via a `needs_audio` property (mirrors `needs_gui`'s `ffi_called`-based precision tracking) — only linked when audio is actually used.
  - `_audio_load_wav` inline helper expands the `Mix_LoadWAV` macro (`SDL_RWFromFile` + `Mix_LoadWAV_RW`) by hand in both Windows (Win64 ABI) and Linux (SysV ABI) targets.

- **Bitmap font rendering** — built-in 8×8 font (`_font8x8`, printable ASCII 32–126) wired into both graphics backends:
  - `framebuffer.Framebuffer.draw_char(x, y, ch, color, scale=1)` / `.draw_text(x, y, text, color, scale=1)` — bitmap text directly on a memory-mapped framebuffer, with `\n` support and integer pixel scaling.
  - `gui.Canvas.char(x, y, ch, scale=1)` / `.text(x, y, s, scale=1)` — same, rendered with SDL2 `draw_point`/`fill_rect` using the canvas's current draw color.

- **Lumen** — the graphics/audio/input ecosystem (`gui` + `framebuffer` + `audio`) now has a name. No new import; it's a branding pass across the existing module docstrings.

- **Live input state in `gui`**: `Canvas.key_down(scancode)` (polling key state, independent of the event queue), `Canvas.mouse_dx()`/`.mouse_dy()` (relative mouse motion), `Canvas.relative_mouse(enabled)` (capture + hide cursor for FPS-style look controls), `Canvas.show_cursor(visible)`.

- **Runtime window control in `gui`**: `Canvas.fullscreen(enabled)` (toggles `WINDOW_FULLSCREEN_DESKTOP`), `Canvas.resize(w, h)`.

- **Clipboard support in `gui`**: `Canvas.set_clipboard(text)` / `.get_clipboard()`.

- **`gui.Font` — TrueType text via SDL2_ttf**, a smooth/anti-aliased alternative to the bitmap font:
  - `Font(path, ptsize)` — load a `.ttf`; `.set_style(style)` (`FONT_STYLE_NORMAL`/`BOLD`/`ITALIC`/`UNDERLINE`, OR-able); `.size(text)` returns `(w, h)` pixel dimensions; `.close()`.
  - `Canvas.draw_ttf(font, x, y, text, color)` — renders blended (anti-aliased) text and blits it in one call.
  - `-lSDL2_ttf` is auto-linked via a `needs_ttf` property, following the same `ffi_called`-based precision tracking as `needs_gui`/`needs_audio`.
  - Requires SDL2_ttf installed (Linux: `libsdl2-ttf-dev`; Windows: `SDL2_ttf.dll` next to the executable).

- **Joystick/gamepad input in `gui`**: `gui.num_joysticks()`, `gui.Joystick(index)` with `.name()`, `.num_axes()`, `.num_buttons()`, `.axis(i)`, `.button(i)`, `.close()`; `gui.joystick_update()`. Auto-initializes the `SDL_INIT_JOYSTICK` subsystem on first use. New event constants `EVENT_JOYAXISMOTION`/`EVENT_JOYBUTTONDOWN`/`EVENT_JOYBUTTONUP`.

- **Rotated/flipped sprite blits**: `Canvas.blit_ex(img, x, y, w, h, angle_deg, flip)` wraps `SDL_RenderCopyEx` — rotate (clockwise degrees) and/or flip (`FLIP_NONE`/`FLIP_HORIZONTAL`/`FLIP_VERTICAL`) a scaled sprite in one call. New `_gui_render_copy_ex` inline helper handles the Win64/SysV argument-passing mismatch (angle is the 5th positional argument, which Win64 always spills to the stack regardless of type, while SysV's independent int/float register counting still fits it in `xmm0`).

- **Cropped blits**: `Canvas.blit_region(img, sx, sy, sw, sh, dx, dy)` draws a sub-rect of a texture (e.g. one frame of a sprite sheet) at its natural size via a new `_gui_render_copy_region` helper.

- **`gui.Tilemap`**: a grid of tile indices into a single spritesheet `Image`. `Tilemap(sheet, tile_w, tile_h, cols, rows)`, `.set(col, row, tile_index)`, `.get(col, row)`, `.draw(canvas, x, y)` (renders the whole grid via `blit_region`).

- **`framebuffer.Framebuffer.text(...)`**: short alias for `draw_text(...)`, matching `gui.Canvas.text()`'s naming.

### Fixed

- `_BUNDLED_SOURCE_STDLIB` was missing `"audio"`, so `import audio` silently compiled to dead code (no error, no real calls) instead of merging the real module source. Added `"audio"` and `"_font8x8"` to the bundled-source stdlib list.

### Tests

453/453 passing (was 448 at 1.1.0).

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
