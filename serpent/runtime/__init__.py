"""Pre-built native runtime helpers for mamba programs.

Each compiled mamba program would otherwise inline ~400 lines of runtime
helpers (dict ops, exception handling, list append/pop, int<->str helpers).
At program scale that gets repetitive and noisy. This package extracts the
runtime into a static archive (`libmamba_rt_<target>.a`) that's assembled
once and linked into every program.

The runtime is built on demand from the same Python codegen helpers used by
the main compiler — see `runtime.build.build_runtime()`. Cached objects live
in `mamba/runtime/_build/`.
"""
