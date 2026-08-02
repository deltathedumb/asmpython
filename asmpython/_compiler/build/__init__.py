"""The build system: everything that decides HOW a compile runs.

Options and profiles that reach every backend, the plan and report that make a
build observable, lockfiles and artifact verification that make it
reproducible, the cache, and the preflight negotiation that rejects an
impossible target/toolchain combination before any work is done.

Distinct from the compiler beside it: nothing here lowers or emits anything.
It decides what to run, records what ran, and refuses what cannot.
"""
