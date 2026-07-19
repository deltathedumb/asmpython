# Design Issue: PyPI Package Resolution

## Current State (as of 2026-07-19)

The `asmpython pypi install/uninstall/list` commands (commit `b3dc5258`) maintain a **separate package installation system** from Python's standard pip:
- Packages are installed to `.asmpython_pypi_packages.json` manifest
- Separate from `pip`'s site-packages
- Only used by pyinbin fallback as import roots

## Correct Design (User's Intent)

asmpython should integrate with **Python's own package ecosystem**:

1. **Use `pip install`** — packages go into Python's site-packages (standard location)
2. **Native compiler resolves from site-packages** — when compiling `import six`, look up six from site-packages first (after checking asmpython stdlib)
3. **Pyinbin also uses site-packages** — for runtime imports
4. **Only dynamic imports use pyinbin** — `importlib.import_module()` evaluated at runtime → handled by pyinbin interpreter

## Why This Matters

- **User expectation**: `pip install six; asmpython build myscript.py` should just work
- **No separate install step**: Don't require `asmpython pypi install six` alongside pip
- **Correct fallback semantics**: native compilation for static imports, pyinbin only for dynamic ones
- **Python ecosystem alignment**: asmpython becomes a Python compiler, not a fork of Python's packaging

## Current Gap

`program.py`'s `_resolve_user_module()` only searches the user's project directory. It needs to also search Python's site-packages via `sysconfig.get_path("purelib")` or similar.

## Recommendation

The `asmpython pypi install` command should either:
1. **Be deprecated** — rely on pip entirely, or
2. **Become a convenience wrapper** — just runs `pip install` and prints the location

Either way, the native compiler and pyinbin need to resolve from site-packages as their primary import source (after asmpython's own stdlib override).

## Related Work

- sema.py already has `_load_module()` for FFI binding lookup, but this is only for stdlib modules with `STDLIB_BINDINGS`
- Need parallel logic for user-installed packages: site-packages resolution with fallback to pyinbin for uncompilable cases
