# Somnia integration policy

Somnia Engine (`deltathedumb/somnia`) is a first-class downstream acceptance project for asmpython.

## Standing rule

When clean, valid Python used by Somnia works under CPython but is blocked by asmpython, implement the missing behavior in asmpython rather than weakening Somnia's architecture.

This includes:

- whole-program import and package resolution,
- language semantics,
- bundled standard-library compatibility,
- runtime helpers,
- native ABI and FFI support,
- linker and platform behavior,
- diagnostics for genuinely unsupported behavior.

Every fix must include:

1. A focused case in `tests/cases/` or `tests/cases_fail/`.
2. The smallest useful compiler/runtime change rather than a Somnia-specific hardcode.
3. Re-execution of the unchanged Somnia CPython/asmpython differential case.
4. Explicit classification if the next failure is a separate compiler, runtime, FFI, or linker gap.

Somnia workarounds are acceptable only for behavior intentionally outside asmpython's scope or an unavoidable external dependency constraint. They must be documented and removed once the upstream capability exists.

## Current acceptance case

The immediate blocker is ordinary user-package import resolution. A script outside the `somnia` package uses:

```python
from somnia import DataModel
```

CPython resolves the package and class normally. asmpython must recursively discover and merge the package's `__init__.py`, re-exported modules, classes, functions, and required module globals rather than treating `DataModel` as an unresolved native symbol.
