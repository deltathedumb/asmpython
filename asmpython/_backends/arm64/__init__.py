"""
asmpython's ARM64 (AArch64) backend -- Stage 1, in progress.

See roadmap.md's "ARM64 support" section for what Stage 0 (toolchain
bring-up) confirmed and what Stage 1 (this package) still needs before
`--backend arm64` is real. Implemented so far: `encoder.py` (instruction
encoding, independently verified against AArch64 assemblers), `regalloc.py`
(linear-scan allocation over AAPCS64's register set), `codegen.py` (the full
current IR instruction-selection surface, including signed frame/spill access),
and a minimal `elf.py` relocatable-object writer for the relocation types the
code generator emits.

The backend remains intentionally unwired from driver.py until its ELF link and
execution probe is green and the AArch64 runtime object/ABI shims exist.
"""
from __future__ import annotations
