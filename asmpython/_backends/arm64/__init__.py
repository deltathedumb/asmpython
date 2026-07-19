"""
asmpython's ARM64 (AArch64) backend -- Stage 1, in progress.

See roadmap.md's "ARM64 support" section for what Stage 0 (toolchain
bring-up) confirmed and what Stage 1 (this package) still needs before
`--backend arm64` is real. Done so far: `encoder.py` (instruction
encoding, verified bit-for-bit against real `aarch64-linux-gnu-as`
output — see `_verify_encoder.py`), `regalloc.py` (linear-scan register
allocation over AAPCS64's register set), and the first complete
`codegen.py` instruction-selection pass. The code generator is still under
verification and is not wired into driver.py yet. Remaining major work:
AArch64 ELF relocation/object writing, runtime object/ABI shims, and driver
integration.

`regalloc.py` deliberately represents spills and locals as negative
X29-relative offsets. Importing this package installs the signed/unscaled
stack-memory wrappers from `_stack_access.py` before `codegen.py` imports
its encoder helpers, preventing those offsets from being sent to A64's
unsigned scaled LDR/STR forms.
"""
from __future__ import annotations

from ._stack_access import install as _install_stack_access

_install_stack_access()
del _install_stack_access
