"""APIR -- A Portable Intermediate Representation.

THE NAME EXPANDS TO NOTHING ABOUT PYTHON, and that is the point rather than a
coincidence. This IR is small, typed and language-independent; the Python
frontend is its first client and is not meant to be its only one, so naming it
after the compiler it ships in would have made the claim and then quietly
contradicted it. The four letters are the container's magic bytes too -- see
`backends/apir/emit.py`, where they were already written down.

WHAT IS NOT IN HERE IS THE CLAIM. This package held the Python object model's
host implementation -- 16,168 lines against 1,568 lines of actual IR, and one
of them importing `asmpython.frontends.python.methods`, the bottom layer of the
compiler reaching for the top. Those live under `objects/` now, beside the C
and subset arrangements of the same runtime, and
`tests/asmpython/unit/test_ir_is_agnostic.py` is what keeps them out: the IR
imports `diagnostics` and nothing else, and never names the object runtime's
symbol prefix. The interpreter is the one exemption and that test says so --
including, as it happens, catching an earlier draft of this very paragraph for
quoting the prefix it was describing.

Read `opcodes.py` first: it is the whole instruction set and the single source
of truth for the verifier, the printer, the parser, the interpreter and the
docs. A backend author should need no second document.

    from asmpython.ir import types as T
    from asmpython.ir import Module, Function, Builder, verify

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
