"""asmpython's x86 (32-bit / IA-32) backend (driver.py's --backend x86).

Under active construction -- see encoder.py (instruction encoding, complete
and NASM-cross-verified) for the first implemented piece. regalloc.py,
codegen.py, and elf.py/coff.py (32-bit object-file writers) do not exist
yet. --backend x86 still resolves to the scaffold below until this backend
is complete enough to replace it -- do not remove this fallback before
compile()/link() are real.
"""

from ..scaffolds import SCAFFOLD_BACKENDS

__module_backend__ = SCAFFOLD_BACKENDS["x86"]
backend = __module_backend__

__all__ = ["__module_backend__", "backend"]
