"""Pre-built native runtime helpers for serpent programs.

Each compiled serpent program would otherwise inline ~400 lines of runtime
helpers (dict ops, exception handling, list append/pop, int<->str helpers).
At program scale that gets repetitive and noisy. This package extracts the
runtime into a static archive (`libserpent_rt_<target>.a`) that's assembled
once and linked into every program.

The runtime is built on demand from the same Python codegen helpers used by
the main compiler — see `runtime.build.build_runtime()`. Cached objects live
in `serpent/runtime/_build/`.
"""
