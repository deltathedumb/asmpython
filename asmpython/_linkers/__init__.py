"""asmpython's pluggable linkers.

A linker is a plain module (or any object exposing the same shape) with:
  requested_args: list[dict]        (optional -- CLI args it wants, default [])
  def link(ctx: dict) -> bytes       (required -- performs the link, returns
                                       the final output file's bytes)

`ctx` carries everything a linker might need: at minimum `objects` (a list
of object-file bytes to link) and `target_os`; individual linkers read
whatever else they require out of it (e.g. `entry_symbol`, `gcc_path`,
`extra_args`, `out_path`).

Backends pick which linker to use (see asmpython/_backends/x86_64's
default_linker/run_backend_link) -- this package just holds the
implementations, named by how driver.py's --linker flag refers to them.

The two built-in linkers (`gcc`, `builtin`) stay hardcoded if/elif in
run_backend_link -- a CPython-hosted-only micro-optimization inherited from
before this registry existed, not required. `_REGISTRY` below is where a
third-party linker registers (see `asmpython.Linker(...)`, the public
authoring API in `asmpython/extend.py`), reached via `register_linker()`
and consulted by `run_backend_link` as the fallback after the two builtins.
This registry is plain-Python-dict-based (CPython-hosted compilation only)
-- NOT yet safe to self-host, since it holds live module/callable
references and asmpython's self-hosted subset has no first-class module
values today (see RESUME.md's "Pending 2.0.0 workload" section, "First-class
module values").
"""

from __future__ import annotations

_REGISTRY: dict = {}


def register_linker(name: str, linker: object) -> None:
    """Register a third-party linker under `name`, making it reachable via
    `--linker NAME`. `linker` must expose `link(ctx: dict) -> bytes` and
    may optionally expose `requested_args: list[dict]`."""
    _REGISTRY[name] = linker


def get_linker(name: str) -> object | None:
    """Look up a registered third-party linker by name, or None."""
    return _REGISTRY.get(name)


def registered_names() -> list[str]:
    return list(_REGISTRY.keys())
