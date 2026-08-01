"""A small, language-independent IR and the tooling around it.

Read `ops.py` first -- it is the whole instruction set and the single source of
truth for the verifier, the text format, the interpreter and the docs.

    from ir import types as T
    from ir.core import Module, Func, Builder
    from ir.verify import verify
"""
from . import types              # noqa: F401
from .core import (              # noqa: F401
    Block, Builder, Func, Global, Instr, Module,
)
from .ops import Op              # noqa: F401
from .verify import VerifyError, verify   # noqa: F401

__all__ = [
    "types", "Op", "Instr", "Block", "Func", "Global", "Module", "Builder",
    "verify", "VerifyError",
]
