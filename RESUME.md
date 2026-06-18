# Selfhost Debug Resume

## Version
Bumped to `2.0.0-preview` (not 1.3.0) — ARM64/macOS platform work requires
restructuring codegen around an IR layer, not a parallel target subclass.
See `roadmap.md` for the full reasoning. Selfhosting is a stretch goal for
this release, not the committed scope.

## What's Working
- `--help`, `--version`: ✓
- Missing file error: ✓
- The Python-hosted compiler (`py -m asmpython ...`) compiles and correctly
  runs real programs exercising dict/list/str/chr/os/time/random and the
  fixes below — verified directly, no crash.

## Selfhost Binary Status
Still segfaults compiling `test_simple.py`, but the bug has moved each time
a fix lands — confirmed via gdb backtraces, not guesswork. Fixed so far,
in order found:

1. **Win64 shadow-space violations** — hand-rolled runtime helpers
   (`_runtime_chr`, `_runtime_zalloc`, several `_runtime_dict_*`,
   `_runtime_list_reverse`, `_runtime_str_strip`/`_str_splitlines`,
   `_runtime_input`, `_math_ldexp`, `_random_random`, `_gui_load_bmp`,
   `_gui_joystick_*`, `_audio_load_wav`, `_threading_lock_*`,
   `_time_sleep_ms`) allocated only 16–32 bytes before calling external C
   functions, which need 32 bytes of shadow space minimum. Raised all to
   48+ bytes. Also: `emit_func_prologue`/`emit_entry_prologue` in
   `target_windows.py` now enforce a 48-byte frame floor for every
   compiled function, not just hand-written helpers.

2. **`@dataclass` synthesized `__init__` ignored `field(default_factory=...)`**
   (`sema.py` ~line 1898) — substituted literal `0` instead of calling the
   factory, so e.g. `Scope()` got `self.types == 0` instead of `{}`. Fixed
   to emit `A.DictLit`/`A.ListLit`/`A.SetLit` matching the requested factory.

3. **Shared AST node across call sites for omitted-argument defaults**
   (`sema.py` `_bind_args` ~line 5874) — the same default expression object
   was reused for every call site omitting that argument; codegen keys
   per-literal frame slots off `id(node)`, so two call sites collided on
   one slot. Fixed with `copy.deepcopy` per call site.

4. **NULL-pointer crash in str/container truthiness** (`codegen.py`,
   `_gen_truthy_test` ~line 11078, the `not` operator ~line 3371, and the
   `bool()` builtin ~line 12416) — truthiness checks for `str` read the
   first byte to detect empty-string falsiness, and for
   `list`/`tuple`/`dict`/`set` read the length at `[ptr+8]`; both assumed
   the pointer is never NULL. `Optional[...]` holding `None` is NULL and
   also falsy, so all three sites now test for NULL first.

5. **Whole-program merge ordering bug** (`program.py` `_merge_import_bindings`)
   — naively hoisted a module's own zero-free-name-dependency constant
   (e.g. a bare string) at discovery-order position, even when some OTHER
   module needed that exact name earlier via a value import
   (`from .. import __version__`). The dependency-aware pass that runs
   after (`_materialize_value_imports`) saw the name already present and
   didn't re-order it. Fixed by collecting every name targeted by any
   module's relative value import upfront and skipping the naive hoist
   for those names entirely.

6. **Class-var inheritance gap in constructor codegen** (`codegen.py`
   `_gen_constructor` ~line 13198, and the matching `_collect_locals`
   pre-pass ~line 1495) — only seeded class-body constants from the
   exact instantiated class, never walking the inheritance chain. A class
   var declared only on a base class (e.g. `Codegen.section_bss`, never
   overridden by `WindowsCodegen`/`LinuxCodegen`) was never written into
   the instance dict, so `self.section_bss` read back as NULL. Fixed by
   walking `_resolve_class_chain` grandparent-first in both places.

7. **`__file__` always compiled to a hardcoded empty string** (`codegen.py`
   — a deliberate scope limitation per its own comment, not a regression).
   `driver.py`'s own `_resolve_tool` does
   `Path(__file__).resolve().parents[2]` to find the repo root; with
   `__file__` always `""`, `.resolve()` fell back to the current working
   directory instead of the real source location, producing a malformed
   path whose `__str__()` returned NULL downstream. Fixed by threading
   `entry_path` (already computed in `__main__.py`) through
   `compile_source`/`compile_targets`/`_run_backend` into the
   `Codegen.__init__` constructor as `self.entry_path`, used by the
   `__file__` codegen path instead of always interning `""`.

   Also implemented while chasing this: `Path.parents` (`pathlib.py`) —
   the indexable parent-walking sequence didn't exist at all; unknown
   attributes silently fall through to a `0`/NULL fallback rather than
   erroring, which is how this stayed hidden until selfhost exercised it.

Each fix was verified independently with a minimal repro compiled via the
Python-hosted compiler before paying for a ~10-60 min selfhost NASM
rebuild (highly variable — confirmed CPU-bound via PowerShell
`Get-Process`, not stalled, just slow on this 434K-line file; don't try
to diagnose further, just budget for the wait). Don't run multiple
builds in parallel, they contend for the same intermediate files and
produce misleading partial results.

**Tooling gotcha discovered repeatedly this session**: the Bash tool's
filesystem view (`ls`, `ps`) can show stale/cached results that lag
significantly behind reality on this Windows/WSL bridge — twice, a
build had actually finished (confirmed by file timestamp) up to ~2 hours
before repeated Bash polling stopped showing 0 bytes. PowerShell's
`Get-Process`/`Get-Item` are more reliable for confirming real-time
state; prefer those, or just trust the Monitor tool's notification
instead of re-polling Bash repeatedly.

## Next Debugging Step
v7 (all 7 fixes above) still segfaults on test_simple.py — one more
distinct, not-yet-isolated bug. Same loop as before:
```
py -m asmpython asmpython/_compiler/__main__.py --emit-asm -o build/asmpython_vN.exe
nohup "/c/Program Files/NASM/nasm.EXE" -f win64 -w-label-redef-late build/asmpython_vN.asm -o build/asmpython_vN.obj > build/nasm_vN.log 2>&1 &
# wait for the .obj to be non-zero (verify via PowerShell Get-Item, not just Bash ls), then:
gcc build/asmpython_vN.obj -o build/asmpython_vN.exe -mconsole
./build/asmpython_vN.exe test_simple.py -o build/out.exe   # exit 139 = still crashing
gdb -batch -ex run -ex bt -ex "x/10i \$pc-20" -ex "info registers" --args build/asmpython_vN.exe test_simple.py -o build/out.exe
```
Match the crash address back to a label in the `--keep-assembly` `.asm`,
then back to the Python source line via context (string labels, runtime
helper names called nearby, surrounding loop/branch shape). If the crash
is `_runtime_str_concat`/`strlen` with a NULL operand again, check
`_resolve_tool` and `driver.py` further — there may be more real-Python
features it depends on that asmpython doesn't fully implement yet
(this session found two: `Path.parents`, real `__file__`).

Paused here per user direction — selfhosting is a stretch goal, not
committed 2.0.0 scope. Resume this loop only when asked; `import_binary`
(below) shipped in this same session, so the real remaining priority is
the platform/optimization roadmap.

## Done since last update
**`import_binary` / `@handle.imported`** — full runtime dynamic-loading
support shipped (see project-1.3-dynamic-import in assistant memory for
the full design notes). `from asmpython import import_binary`,
`handle = import_binary("path.dll")`, `@handle.imported` on a stub
function (its own annotations are the only contract — int/float/str
params only, no containers), `handle.func(args)` calls through the
resolved pointer. LoadLibraryA+GetProcAddress on Windows, dlopen+dlsym
on Linux (`-ldl` added to the link command). Verified end-to-end against
`msvcrt.dll`'s `toupper` on Windows; test at
`tests/cases/299_import_binary.py` also covers the Linux path via
`libc.so.6` (untested on this machine — Windows-only environment).
Known gap: no `symbol=` override yet (unlike `@asm_func(symbol=...)`),
so the Python function name must exactly match the real exported C
symbol; only scalar parameter types are supported.

## Done since last update (round 2)
**Peephole pass** (`codegen.py` `_peephole_optimize`, commit `ac520ff7`) —
`generate()` now eliminates a `mov reg, X` immediately followed by
`mov reg, Y` to the same register, since the first write is dead in
straight-line flow. Caught a real correctness bug in the first version
before committing: the naive "same destination register" check missed
that `mov rdx, [ptr]` / `mov rdx, [rdx+8]` (pointer-chasing — extremely
common) is NOT a dead store, since the second line's source operand
reads the value the first line just loaded. The full test suite caught
it immediately (27/454 vs. 454/454) — the fix checks whether the second
line's source operand text contains the destination register as a
token before allowing removal. Yield on the real codebase is modest
(~20 lines out of ~447K on the selfhost asm) once pointer-chasing is
correctly excluded — the earlier "~1,905 pairs" estimate from session 1
was the naive/buggy heuristic's count, not the safe one. Kept anyway:
free, zero-risk now, and a base for future peephole patterns.

Also fixed in passing: `Codegen` (base class) was missing
`_emit_load_library`/`_emit_get_proc_addr` stubs — only the
`WindowsCodegen`/`LinuxCodegen` subclasses had them, but
`_gen_import_binary` (defined on the base class) calls both, which
sema only caught when compiling something through the base-class
static type (i.e. selfhost). Added `raise NotImplementedError` stubs
matching the existing `_emit_os_getcwd`-style pattern.

## Pending
1. Continue selfhost debugging (non-blocking bonus, not release scope)
2. ARM64 codegen (IR layer) — blocks Pi, Apple Silicon, most of Android
3. macOS Intel x86_64 (medium-large, reuses target_linux.py's SysV/libc approach)
4. Garbage collector (refcounting — large, self-contained to x64 targets)
5. More peephole patterns if revisited: redundant `push X`/`pop X` pairs
   and self-moves were both checked and found negligible on the current
   codebase (0 and 1 instance respectively) — not worth a dedicated pass
   on their own, but cheap to fold in if another pattern justifies a
   second look at the instruction stream
6. Add CODE_OF_CONDUCT.md, CONTRIBUTING.md, SECURITY.md, issue templates
