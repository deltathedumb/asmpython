# Session Resume — parity-expansion branch

## Standing directives (apply to all future work on this branch)

- Push after every commit.
- Prefer breadth over depth — fill many small stdlib/language gaps rather
  than going deep on one feature.
- Ship full implementations; no stubs or partial work.
- Never use `-> "ClassName"` quoted forward-reference annotations.
- Delete all temporary/scratch test files (`_tmp*.py/.asm/.obj/.exe`) after
  use — don't leave them in the repo or commit them. `.gitignore` already
  covers `_scratch*.*`, `_probe*.*`, `_tmp*.*`.

## Current status

- 446/446 tests passing.
- Self-host Windows build passes (`build.py` → `asmpython.exe` compiles clean).
- Linux cross-build via WSL fails unrelated to our work (WSL not available).

## Fixes landed this session (commit e8e4a3ce)

### Self-host sema errors — all resolved

Three root causes fixed:

1. **`subprocess.run()` missing `text`/`env` kwargs** — added `text: int = 0`
   and `env: int = 0` to `stdlib/subprocess.py`'s `run()` stub so `driver.py`
   and `_runtime/build.py` don't get "unexpected keyword argument" errors.

2. **Module alias "A" shadowed by `re.py`'s `A: int = 256`** — the
   whole-program loader materialised `re.py`'s single-letter regex flag
   constants as globals before `from . import ast_nodes as A` could bind "A"
   as a module alias. Fixed in two places:
   - `sema.py`: `from . import X` (no module, bind_ty="module") now
     ALWAYS overrides any prior binding, so stale int constants can't
     shadow module aliases.
   - `program.py`: `FromImport` bound names are now added to `available`
     after each import is collected, preventing later constant-assignment
     scans from claiming the same name first.

## Fixes landed this session (commit 8b49d79e)

- `yield` in `for` loops — generator transform now handles both `range_args`
  and `iter` for-loops. Materialises iterable as list in `__init__` + `_idx`
  counter; `__next__` bounds-checks, binds loop var, runs body, returns.
- `--onedir` implies `--use-runtime-lib` at the `compile_source` API level.

## Other known gaps not yet started (for next breadth pass)

- `yield` inside `if` branches (yield not at top level of loop body).
- Dict comprehensions with non-string keys.
- Nested tuple unpacking `(a, b), c = ...`.

## Immediate next step

Pick any item from the breadth backlog above and implement it.
