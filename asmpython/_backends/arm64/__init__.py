"""
asmpython's ARM64 (AArch64) backend -- Stage 1, in progress.

See roadmap.md's "ARM64 support" section for what Stage 0 (toolchain
bring-up) confirmed and what Stage 1 (this package) still needs before
`--backend arm64` is real: `encoder.py` (instruction encoding, done for
the initial instruction set — see `_verify_encoder.py`) is only the first
piece. `regalloc.py` (register allocation over AAPCS64's register set)
and `codegen.py` (IR op -> AArch64 instruction selection), plus AArch64
relocation support in an ELF object-file writer and AArch64 ports of the
runtime object/ABI shims, are not started. This module is intentionally
NOT wired into driver.py's `--backend` dispatch yet — there is nothing
here yet that can compile a real program.
"""
from __future__ import annotations
