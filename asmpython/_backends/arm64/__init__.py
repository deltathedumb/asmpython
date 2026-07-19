"""
asmpython's ARM64 (AArch64) backend -- Stage 1, in progress.

See roadmap.md's "ARM64 support" section for what Stage 0 (toolchain
bring-up) confirmed and what Stage 1 (this package) still needs before
`--backend arm64` is real. Done so far: `encoder.py` (instruction
encoding, verified bit-for-bit against real `aarch64-linux-gnu-as`
output — see `_verify_encoder.py`) and `regalloc.py` (linear-scan
register allocation over AAPCS64's register set, ported from the x86-64
backend's allocator and smoke-tested). Still needed: `codegen.py` (IR op
-> AArch64 instruction selection — the biggest remaining piece), AArch64
relocation support in an ELF object-file writer (`EM_AARCH64`,
`R_AARCH64_*`), and AArch64 ports of the runtime object/ABI shims. This
module is intentionally NOT wired into driver.py's `--backend` dispatch
yet — there is nothing here yet that can compile a real program.
"""
from __future__ import annotations
