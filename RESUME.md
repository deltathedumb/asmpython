# Selfhost Debug Resume

## Current State
Debugging why `asmpython-1.3.0-x86_64.exe` (selfhost binary) crashes when actually compiling Python source files.

## What's Working
- `--help`, `--version`: ✓
- Missing file error (`asmpython: source file not found: ...`): ✓
- Crash happens AFTER `args.source.exists()` succeeds but DURING compilation of any real `.py` file

## Fixes Already In (committed to branch `parity-expansion`)
1. **`program.py` `_dedupe_lifted_funcs`** — was mutating shared `taken_names`, dropping all lifted functions from imported modules. Fixed with `local_names` set.
2. **`target_windows.py` `___chkstk_ms` stack probe** — any frame > 4096 bytes on Windows needs `___chkstk_ms` probe before `sub rsp, N`. Added `_emit_stack_probe_if_needed()` called from both `emit_entry_prologue` and `emit_func_prologue`. 13 large frames in selfhost ASM now all have probes.
3. **Version bumped to 1.3.0**, CHANGELOG updated.

## Current Investigation
Tracing the crash in `build/asmpython-selfhost-debug.asm` (fresh build with `--keep-assembly`).

- `main:` entry at line 64
- `userfn_main:` at line 40827 — frame is only 1200 bytes (no stack probe needed)
- `ArgumentParser__parse_args` call at line 40840 — works (file-not-found branch proven)
- `Path__exists` call at line 40931 — works
- `detect_default_target` call at line ~40972 — to be verified
- `Path__read_text` call at line ~40988 — suspect crash site

Next step: read lines 40945–41100 of `build/asmpython-selfhost-debug.asm` to trace the path after the file-exists check, find where the crash occurs.

## Key Files
- `asmpython/_compiler/target_windows.py` — stack probe fix
- `asmpython/_compiler/program.py` — `_dedupe_lifted_funcs` fix
- `asmpython/stdlib/argparse.py` — `_convert()`/`parse_args()` being inspected
- `asmpython/stdlib/pathlib.py` — `Path.read_text` at line 177
- `build/asmpython-selfhost-debug.asm` — fresh selfhost ASM, `userfn_main` at line 40827
- `build/asmpython-selfhost-debug.exe` — crashing selfhost binary
- `build/asmpython-selfhost3.asm` — older selfhost ASM for reference

## 1.3.0 Scope (from user)
Per user's message: 1.3.0 must also include **ARM support, Android support, Mac support, garbage collector, and optimizations** — none of these are implemented yet. Current blocker is the selfhost crash.

## Pending After Selfhost Fix
1. Commit + push 1.3.0 to `origin/beta`
2. Implement ARM/Android/Mac target support
3. Implement garbage collector
4. Implement optimizations
5. Update `roadmap.md` and documentation
