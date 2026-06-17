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

Each fix was verified independently with a minimal repro compiled via the
Python-hosted compiler before paying for a ~10-15 min selfhost NASM
rebuild. The NASM step on the full 434K-line selfhost `.asm` is slow
(confirmed CPU-bound, not antivirus — `-O0` made no difference) but does
complete; don't run multiple builds in parallel, they contend for the
same intermediate files and produce misleading partial results.

## Next Debugging Step
Latest crash (v3 build, before fix #4 above existed) was a different,
not-yet-isolated site. Re-run with all four fixes combined:
```
py -m asmpython asmpython/_compiler/__main__.py --emit-asm -o build/asmpython_vN.exe
nohup "/c/Program Files/NASM/nasm.EXE" -f win64 -w-label-redef-late build/asmpython_vN.asm -o build/asmpython_vN.obj > build/nasm_vN.log 2>&1 &
# wait for the .obj to be non-zero, then:
gcc build/asmpython_vN.obj -o build/asmpython_vN.exe -mconsole
./build/asmpython_vN.exe test_simple.py -o build/out.exe   # exit 139 = still crashing
gdb -batch -ex run -ex bt -ex "x/10i \$pc-20" -ex "info registers" --args build/asmpython_vN.exe test_simple.py -o build/out.exe
```
Match the crash address back to a label in the `--keep-assembly` `.asm`,
then back to the Python source line via context (string labels, runtime
helper names called nearby, surrounding loop/branch shape).

## Pending
1. Continue selfhost debugging (non-blocking bonus, not release scope)
2. ARM64 codegen (IR layer) — blocks Pi, Apple Silicon, most of Android
3. macOS Intel x86_64 (medium-large, reuses target_linux.py's SysV/libc approach)
4. Garbage collector (refcounting — large, self-contained to x64 targets)
5. Optimizations: NASM `-Ox` is already default (confirmed no-op to add
   explicitly); peephole pass on emitted instructions is genuinely new and
   cheap (found ~1,905 safe dead-store `mov`-then-`mov`-same-register pairs
   in the selfhost asm as a concrete starting pattern)
6. Add CODE_OF_CONDUCT.md, CONTRIBUTING.md, SECURITY.md, issue templates
