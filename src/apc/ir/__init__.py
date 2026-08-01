"""A small, language-independent IR.

Read `opcodes.py` first: it is the whole instruction set and the single source
of truth for the verifier, the printer, the parser, the interpreter and the
docs. A backend author should need no second document.

    from apc.ir import types as T
    from apc.ir import Module, Function, Builder, verify

Three decisions shape everything here:

  * The TYPE IS A FIELD on the instruction, not part of the mnemonic, so
    `%3 = i64.add %1, %2` puts everything a backend needs on one line while
    the opcode table stays at 39 entries.
  * SIGNEDNESS LIVES ON THE TYPE. One DIV, its meaning read from `ty`.
  * REGISTERS ARE MUTABLE and there are no phi nodes; a frontend joins paths
    by assigning the same register on both.
"""
from . import types
from .builder import Builder
from .cfg import ControlFlowGraph
from .module import (
    Block, Function, Global, Instruction, Linkage, Module, Register,
)
from .opcodes import Op, Spec, spec
from .printer import parse_module, print_func, print_module
from .verifier import VerifyError, verify

__all__ = [
    "Block", "Builder", "ControlFlowGraph", "Function", "Global",
    "Instruction", "Linkage", "Module", "Op", "Register", "Spec",
    "VerifyError", "parse_module", "print_func", "print_module", "spec",
    "types", "verify",
]
