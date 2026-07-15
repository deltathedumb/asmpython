"""Portable bytecode model for pyinbin.

Instructions use integer operands only; names and literal values live in the
containing ``CodeObject`` tables. This keeps serialization independent of a
host Python object graph and maps directly onto the future native VM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Op(IntEnum):
    LOAD_CONST = 1
    LOAD_NAME = 2
    STORE_NAME = 3
    POP_TOP = 4
    BINARY_ADD = 10
    BINARY_SUB = 11
    BINARY_MUL = 12
    BINARY_DIV = 13
    BINARY_FLOORDIV = 14
    BINARY_MOD = 15
    COMPARE_EQ = 20
    COMPARE_LT = 21
    COMPARE_LE = 22
    COMPARE_GT = 23
    COMPARE_GE = 24
    JUMP = 30
    JUMP_IF_FALSE = 31
    CALL = 40
    RETURN = 41
    MAKE_FUNCTION = 42
    MAKE_CLASS = 43
    BUILD_LIST = 50
    BUILD_DICT = 51
    BUILD_TUPLE = 52
    BUILD_SET = 53
    GET_ITEM = 54
    SET_ITEM = 55
    GET_ITER = 56
    FOR_ITER = 57
    GET_ATTR = 60
    SET_ATTR = 61
    IMPORT_NAME = 70
    IMPORT_FROM = 71
    IMPORT_ROOT = 72
    UNARY_NEGATIVE = 80
    UNARY_NOT = 81
    BINARY_POW = 16
    COMPARE_NE = 25
    COMPARE_IS = 26
    COMPARE_IS_NOT = 27
    COMPARE_IN = 28
    COMPARE_NOT_IN = 29


@dataclass(frozen=True)
class Instruction:
    op: Op
    arg: int = 0


@dataclass
class CodeObject:
    name: str
    instructions: list[Instruction]
    constants: list[object] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    arg_names: list[str] = field(default_factory=list)

    def validate(self) -> None:
        for offset, instr in enumerate(self.instructions):
            if not isinstance(instr.op, Op):
                raise ValueError(f"{self.name}: invalid opcode at {offset}")
            if instr.op in (Op.LOAD_CONST, Op.MAKE_FUNCTION, Op.MAKE_CLASS) and not 0 <= instr.arg < len(self.constants):
                raise ValueError(f"{self.name}: constant index out of range at {offset}")
            if instr.op in (Op.LOAD_NAME, Op.STORE_NAME, Op.GET_ATTR, Op.SET_ATTR) and not 0 <= instr.arg < len(self.names):
                raise ValueError(f"{self.name}: name index out of range at {offset}")
            if instr.op in (Op.JUMP, Op.JUMP_IF_FALSE) and not 0 <= instr.arg < len(self.instructions):
                raise ValueError(f"{self.name}: jump target out of range at {offset}")
            if instr.arg < 0:
                raise ValueError(f"{self.name}: negative operand at {offset}")
