"""Registry for third-party compilation backends.

The two built-in IR backends (`x86_64/`, `ternary/`) are NOT registered
here -- `driver.py`'s `_run_backend` special-cases `--backend x86-64` and
`--backend ternary` directly, since each needs bespoke wiring beyond the
plain `IRBackend.compile()`/`.link()` contract (the x86-64 backend in
particular: ABI shim building, runtime object linking, GCC path
resolution). `legacy` (the original NASM-text codegen pipeline) isn't an
`IRBackend` at all -- a wholly different, older pipeline.

This registry is for anything else: a third-party backend conforming to
`asmpython._compiler.ir.IRBackend` (`requested_args`/`default_linker`/
`compile(module, args)`/`link(objects, args)`), registered via
`asmpython.backend.Backend(...)` (the public authoring API in `asmpython/
extend.py`) and reached at build time via `--backend NAME`, routed through
`driver.py`'s `_run_backend_registered` -- a plain compile-then-link-then-
write, mirroring `_run_backend_ternary`'s simple shape.

Plain-Python-dict-based (CPython-hosted compilation only) -- NOT yet safe
to self-host, since it holds live backend objects and asmpython's
self-hosted subset has no first-class module/object values stored in dicts
today (see RESUME.md's "Pending 3.14 workload" section, "First-class
module values").
"""

from __future__ import annotations

_REGISTRY: dict = {}


def register_backend(name: str, backend: object) -> None:
    """Register a third-party backend under `name`, making it reachable via
    `--backend NAME`. `backend` must conform to
    `asmpython._compiler.ir.IRBackend` (`compile`/`link`, and optionally
    `requested_args`/`default_linker`)."""
    _REGISTRY[name] = backend


def get_backend(name: str) -> object | None:
    """Look up a registered third-party backend by name, or None."""
    return _REGISTRY.get(name)


def registered_names() -> list[str]:
    return list(_REGISTRY.keys())
