"""Pre-built native runtime helpers for asmpython programs.

Each compiled asmpython program would otherwise inline ~400 lines of runtime
helpers (dict ops, exception handling, list append/pop, int<->str helpers).
At program scale that gets repetitive and noisy. This package extracts the
runtime into a static archive (`libasmpython_rt_<target>.a`) that's assembled
once and linked into every program.

The runtime is built on demand from the same Python codegen helpers used by
the main compiler — see `_runtime.build.build_runtime()`. Cached objects live
in `asmpython/_runtime/_build/`.
"""
