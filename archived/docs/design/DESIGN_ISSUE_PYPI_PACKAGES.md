# PyPI Package Resolution

## Resolved Design (beta, 2026-07-19)

asmpython now uses Python's package ecosystem directly. There is no independent
PyPI installation store, package manifest, or `pypi_libs` directory.

Install Python dependencies with the interpreter that runs the compiler:

```text
python -m pip install <package>
```

The normal `asmpython` command and `python -m asmpython` then discover those
packages from that interpreter's `site-packages` / `dist-packages` roots.

## Resolution Policy

1. asmpython's bundled source and FFI standard library is authoritative.
2. Non-stdlib project modules resolve as normal project source.
3. Third-party static imports resolve from the active interpreter's pip roots.
4. Only an actual dynamic import operation may enable pyinbin fallback.

Comments, string literals, `eval`, and `exec` do not authorize fallback. Dynamic
operations such as `__import__(name)` and `importlib.import_module(name)` do.

## Native vs. Dynamic Imports

Static imports are compiled through the native whole-program loader. A
pure-Python package installed by pip is parsed and merged like project source,
including package-relative imports and imported module constants.

A pip-installed module that uses syntax outside the native compiler's supported
surface fails with a native-import diagnostic. It is not silently executed by
pyinbin merely because it came from PyPI.

Compiled CPython extension modules (`.pyd`, `.so`, `.dylib`) are rejected
explicitly because asmpython does not implement the CPython C extension ABI.

When a reachable source file performs a dynamic import and native compilation
rejects the program, pyinbin receives the same site-packages roots. Verified
pyinbin bundle modules remain first; a module absent from the bundle may then
resolve from explicit roots and pip-installed source.

## Removed Private System

The public `asmpython pypi install|uninstall|list` path is retired and reports a
migration message directing users to `python -m pip`. The old private helper API
is a non-operational compatibility shim that only raises the same migration
error; it does not download, extract, track, list, or remove package files.

Legacy `project.json` fields `pypi_packages` and `pypi_dir` are ignored as unknown
fields and are no longer emitted when a project manifest is saved.
