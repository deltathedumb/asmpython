# Resume dump — parity-expansion branch, self-hosting bug

## Standing directives (entire branch)
- Push after every commit.
- Prefer breadth over depth.
- Ship full implementations — no stubs/partial work.
- Never use `-> "ClassName"` quoted forward-reference annotations.
- Delete all temp/scratch test files (`_tmp*.py/.asm/.obj/.exe`) after use.

## The bug
"Running the output selfhost binary outputs nothing." Root-caused and fixed a chain of issues; current blocker is the deep one: `sema.py`/`codegen.py` do generic AST metaprogramming with heterogeneous lists that asmpython's type system can't fully express yet.

**User-confirmed direction (do not relitigate):** pursue true full self-hosting — redesign the type system to support heterogeneous AST lists (tagged unions / sum types), not a workaround or frontend-only self-host.

## Fixes verified this session (448/448 tests pass, rebuilt, executed)
1. Dotted-base-class inheritance — fixed.
2. Missing `format_help()` — fixed.
3. `FromImport` misclassification — fixed.
4. Int-keyed dict crash — `asmpython/_compiler/errors.py` — fixed, verified.
5. `os.environ` null deref — `asmpython/_compiler/codegen.py` — fixed, verified.
6. `type=Path` no-op — `asmpython/stdlib/argparse.py` — fixed, verified.
7. `store_true`/`count` always-truthy flags — `asmpython/stdlib/argparse.py` — fixed, verified.
   - `Namespace.__init__` now declares typed fields: `self.source/output/icon/nasm/gcc: Path = None` (typed-but-null, preserves `is None`), `self.check/use_runtime_lib/emit_asm/keep/keep_assembly/one_error/json: int = 0`.
   - `parse_args` stores real ints for `store_true`/`store_false`/`count` instead of strings.
   - `get_int`/`get_flag` simplified to direct `getattr`.
8. **`BoolOp.values` parser bug** — `asmpython/_compiler/parser.py` (~line 151-153) — fixed. `A.BoolOp` (ast_nodes.py:619) has fields `left`/`right`, not `values`; the old free-var-collection code iterated `node.values`, crashing the parse of any file using `and`/`or` with 3+ operands. This silently excluded `codegen.py`/`sema.py` from the self-host bundle via `load_program`'s `except Exception: continue` swallow (program.py:364, ~399-406). Fixing this exposed item below — the real remaining gap.

## Current blocker: 13 sema errors

Ran fresh via `python build.py` just now:

```
asmpython/__main__.py:1194:29: int has no method 'add'
asmpython/__main__.py:1226:29: int has no method 'Attr'
asmpython/__main__.py:1269:29: int has no method 'AttrAssign'
asmpython/__main__.py:587:21:  int has no method 'get'
asmpython/__main__.py:645:9:   [E017] cannot index a int
asmpython/__main__.py:327:35:  [E017] cannot index a int
asmpython/__main__.py:1033:17: Match() got an unexpected keyword argument 'subject'
asmpython/__main__.py:2138:25: list.append() expected instance:Expr, got instance:Name
asmpython/__main__.py:1432:18: mixed list element types (instance:While and instance:Raise); mixed-type lists need a tagged-value runtime, not yet implemented
asmpython/__main__.py:1610:27: list.append() expected instance:AttrAssign, got instance:Assign
asmpython/__main__.py:1669:41: unsupported operand type for +: str + list
asmpython/__main__.py:3312:36: mixed list element types (instance:Assign and instance:ExprStmt); mixed-type lists need a tagged-value runtime, not yet implemented
asmpython/__main__.py:3443:71: mixed list element types (instance:Name and instance:Expr); mixed-type lists need a tagged-value runtime, not yet implemented
```

Note: line numbers are against the **merged self-host bundle** (`asmpython/__main__.py` as seen by `load_program`), not necessarily the original source file line numbers — match by content when investigating.

### Research agent's classification (produced before the pause — re-verify line refs before trusting)
- **~8 of these errors share ONE root cause**: the same bug class as the `BoolOp` fix, but in `_collect_refs_expr` (parser.py ~69-168) for free-variable capture. Missing cases: `A.MethodCall`, `A.Try`, `A.Subscript`, `A.IfExp`, list/dict/tuple/f-string literals, and `A.Call.kwargs` (only `.args` is walked). When a name is referenced only inside one of these unhandled node kinds (e.g. `A` in `A.Attr(...)` — a `MethodCall`), it becomes invisible to closure free-var capture and silently defaults to `int`. Suspected to explain the `int has no method 'X'` / `cannot index a int` errors above. **Do this fix first — smaller, same-bug-class as the already-fixed BoolOp case.**
- **4 errors are genuine heterogeneous-list cases** needing real tagged-union/sum-type support: the three `mixed list element types` errors, plus the `list.append() expected instance:X, got instance:Y` errors (codegen/sema build lists mixing arbitrary `Stmt`/`Expr` subtypes — the core missing language feature per the user's confirmed direction).
- **1 error is a sema list-element-type-tracking gap**: records one concrete subtype instead of a declared sum type.
- `Match() got an unexpected keyword argument 'subject'` and `str + list` need individual look at call sites.

None of this has been implemented yet.

## Two anomalies found in the working tree while preparing this dump — investigated, here's the verdict

### 1. Unauthorized `sema.py` edit, made by a subagent
A subagent spawned earlier for a **read-only scoping survey** (explicitly instructed "do not write or edit any code") nonetheless left real, uncommitted edits in `asmpython/_compiler/sema.py` (~54 lines), discovered via `git diff --stat` while writing this dump. Flagging this plainly — it should not have happened.

What the diff actually does (read in full via `git diff`):
- Two annotation-resolution helpers (~line 1010 and ~1073) now prefer a dotted reference's leaf class if it matches a class the compiler models (e.g. `argparse.ArgumentParser`) **before** falling back to "capitalized external class → opaque instance". Previously `list[argparse.Namespace]` always degraded to an opaque instance, losing field/method info and breaking inheritance-chain resolution for subclasses returned through a base-class-annotated function.
- Import-binding logic (~line 3029) now distinguishes `from . import ast_nodes as A` (aliased sibling-module import → bind `"module"`) from `from .. import __version__` (unaliased bare value import → bind `"any"` without clobbering a concrete type the whole-program loader already materialized). Previously a blunt `"module" if not s.module else "any"` check didn't look at aliasing at all.

**Verified just now: `python tests/runner.py` → 448/448 passed** with this change in place. It reads as genuine and reasoned, not corruption, and it's plausibly relevant to the current 13-error blocker (better dotted-annotation resolution could affect some of those errors). But it's **unauthorized and uncommitted** — your call whether to keep it. Worth re-running the sema repro with vs. without it to isolate its effect on the 13 errors.

### 2. Unexplained `repro_pkg/` directory (untracked)
Same likely origin (subagent side effect). All files read in full, all tiny:
- `repro_pkg/__init__.py` — empty.
- `repro_pkg/ast_nodes.py` — `@dataclass class Foo: x: int; y: int = 0`.
- `repro_pkg/main.py` — `from . import ast_nodes as A` / `def make(x): return A.Foo(x=x, y=1)`.
- `repro_pkg/main2.py` — same shape inside a class method with a nested closure.
- `repro_pkg/main3.py` — adds `import copy; copy.copy(base)`.
- `repro_pkg/main4.py` — nested-closure variant returning `A.Foo`.

Minimal repros for the dotted-annotation/closure-capture bug class — harmless, not part of the package, untracked. Recommend deleting once any findings are folded into the real fix. Not deleted yet, pending your call.

## Uncommitted state right now
- Modified (tracked): `asmpython/_compiler/parser.py` (BoolOp fix), `asmpython/stdlib/argparse.py` (Path/flag fixes), `asmpython/_compiler/sema.py` (unauthorized-but-verified-safe annotation/import fix, see above), plus earlier-segment fixes to `asmpython/_compiler/codegen.py` / `asmpython/_compiler/errors.py` (os.environ lowering, ERROR_DESCRIPTIONS re-keying — reconfirmed present via `git diff --stat`, not modified further this segment).
- Untracked: `repro_pkg/` (see above).
- **Nothing committed yet this entire session.**

## Scratch/debug files to delete before commit (standing directive)
- `build/asmpython_dbg.exe/.asm/.obj`
- `build/asmpython_dbg2.exe/.asm/.obj`
- `build/asmpython_dbg3.exe/.asm/.obj`
- `build/asmpython_dbg4.exe/.asm/.obj`
- `build/asmpython_dbg5.exe/.asm/.obj`
- `_tmp_main_backup.py`
- `_tmp_minimal.py/.exe/.asm/.obj`
- `_tmp_selfhost.py`
- `_tmp_selfhost_test.py`
- `repro_pkg/` (pending your decision — see anomaly #2)

## Next steps (small pieces, per latest instruction)
1. Decide what to do with the two anomalies (keep/revert `sema.py` diff; keep/delete `repro_pkg/`).
2. Extend `_collect_refs_expr` (parser.py ~69-168) for `MethodCall`/`Try`/`Subscript`/`IfExp`/literals/`Call.kwargs` — same bug class as the already-fixed `BoolOp` case. Smallest, highest-leverage next fix.
3. Re-run the sema error repro (see snippet below) to see how many of the 13 errors remain.
4. For remaining genuine heterogeneous-list errors: design + implement tagged-union/sum-type support (runtime tagged representation + sema inference for "this list holds any Stmt/Expr").
5. Fix `Match(subject=...)` kwarg and `str + list` individually.
6. Re-run `python tests/runner.py` (must stay 448/448) and `python build.py` (self-host) after each change.
7. Clean up scratch files, commit, push.

## Direct sema-error repro snippet (bypasses CLI line-number ambiguity)
```python
from pathlib import Path
from asmpython._compiler.program import load_program
from asmpython._compiler.sema import analyze
from asmpython._compiler.errors import MultiSemaError
entry = Path('asmpython/__main__.py').resolve()
src = entry.read_text(encoding='utf-8')
mod = load_program(src, entry)
try:
    analyze(mod, source_dir=entry.parent, collect_errors=True)
    print('CLEAN')
except MultiSemaError as me:
    for e in me.errors:
        print(f'{e.pos.line}:{e.pos.col}: {e.message}')
```
Note: `pos.line` is relative to the node's original source file, not the merged program — match by content/line-count against candidate files.
