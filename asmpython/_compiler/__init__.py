"""asmpython compiler front-end: lex -> parse -> sema -> NASM codegen.

This is the private implementation package. The user-facing API lives in
`asmpython` (the parent package) and `asmpython.assembly`.
"""

from .. import __version__  # re-export the single source of truth
from . import linux_runtime_fixes as _linux_runtime_fixes
from .unpack_normalize import install_ir_lowering_prepass


# Target-neutral IR lowering must see the same statically typed destructuring
# normalization regardless of which backend/driver imports it. The installer is
# idempotent, so direct backend helpers may still invoke it defensively.
install_ir_lowering_prepass()


__all__ = ["__version__"]
