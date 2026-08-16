"""Portable bytecode model for pyinbin.

Instructions use integer operands only; names and literal values live in the
containing ``CodeObject`` tables. This keeps serialization independent of a
host Python object graph and maps directly onto the future native VM.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import IntEnum


class Op(IntEnum):
    LOAD_CONST = 1
    LOAD_NAME = 2
    STORE_NAME = 3
    POP_TOP = 4
    STORE_GLOBAL = 5
    DUP_TOP = 6
    SWAP = 7
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
    JUMP_IF_TRUE = 32
    JUMP_IF_FALSE_KEEP = 33
    JUMP_IF_TRUE_KEEP = 34
    CALL = 40
    CALL_KW = 48
    RETURN = 41
    MAKE_FUNCTION = 42
    MAKE_CLASS = 43
    TRY_BEGIN = 44
    TRY_END = 45
    RAISE = 46
    MATCH_EXCEPTION = 47
    BUILD_LIST = 50
    BUILD_DICT = 51
    BUILD_TUPLE = 52
    BUILD_SET = 53
    GET_ITEM = 54
    SET_ITEM = 55
    GET_ITER = 56
    FOR_ITER = 57
    UNPACK_SEQUENCE = 58
    GET_ATTR = 60
    SET_ATTR = 61
    DELETE_ATTR = 62
    DELETE_NAME = 63
    DELETE_ITEM = 64
    WITH_ENTER = 65
    WITH_EXIT = 66
    ASSERT = 67
    LIST_APPEND = 68
    IMPORT_NAME = 70
    IMPORT_FROM = 71
    IMPORT_ROOT = 72
    IMPORT_RELATIVE_FROM = 73
    IMPORT_STAR = 74
    BUILD_SLICE = 75
    YIELD_VALUE = 76
    MATCH_EXCEPTION_CHECK = 77
    UNARY_NEGATIVE = 80
    UNARY_NOT = 81
    BINARY_POW = 16
    BINARY_BITAND = 17
    BINARY_BITOR = 18
    BINARY_BITXOR = 19
    BINARY_LSHIFT = 100
    BINARY_RSHIFT = 101
    BINARY_BOOL_AND = 102
    UNPACK_EX = 103
    BUILD_LIST_UNPACK = 104
    BUILD_TUPLE_UNPACK = 105
    BUILD_SET_UNPACK = 106
    BUILD_DICT_UNPACK = 107
    SET_ADD = 108
    BINARY_MATMUL = 109
    UNARY_POSITIVE = 110
    UNARY_INVERT = 111
    MATCH_PATTERN = 112
    COMPARE_NE = 25
    COMPARE_IS = 26
    COMPARE_IS_NOT = 27
    COMPARE_IN = 28
    COMPARE_NOT_IN = 29
    AWAIT = 113
    RAISE_FROM = 114


@dataclass(frozen=True)
class Instruction:
    op: Op
    arg: int = 0


def _unwrap_nested_code(item: object) -> object:
    if isinstance(item, tuple) and len(item) in (2, 3) and isinstance(item[0], CodeObject):
        return item[0]
    if isinstance(item, tuple) and len(item) in (3, 4) and isinstance(item[1], CodeObject):
        return item[1]
    return item


@dataclass
class CodeObject:
    name: str
    instructions: list[Instruction]
    constants: list[object] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    arg_names: list[str] = field(default_factory=list)
    kwonly_names: list[str] = field(default_factory=list)
    vararg_name: str | None = None
    kwarg_name: str | None = None
    posonly_names: list[str] = field(default_factory=list)
    is_generator: bool = False
    is_coroutine: bool = False
    free_names: list[str] = field(default_factory=list)
    interactive: bool = False
    is_async_generator: bool = False

    def __getattr__(self, name: str) -> object:
        if name == "co_name":
            return self.name
        if name == "co_filename":
            return "<pyinbin>"
        if name == "co_firstlineno":
            return 1
        if name == "co_argcount":
            return len(self.arg_names)
        if name == "co_kwonlyargcount":
            return len(self.kwonly_names)
        if name == "co_freevars":
            return tuple(self.free_names)
        if name == "co_cellvars":
            return ()
        if name == "co_varnames":
            return tuple(self.arg_names)
        if name == "co_consts":
            items = [_unwrap_nested_code(item) for item in self.constants]
            nested = [item for item in items if isinstance(item, CodeObject)]
            if nested and len(items) > 1 and not isinstance(items[1], CodeObject):
                first = items[0]
                rest = [item for item in items[1:] if not isinstance(item, CodeObject)]
                items = [first, nested[0], *rest, *nested[1:]]
            return tuple(items)
        if name == "co_names":
            return tuple(self.names)
        if name == "co_code":
            return b""
        if name in {"co_linetable", "co_lnotab", "co_exceptiontable"}:
            return b""
        if name == "co_flags":
            return 0
        if name == "co_nlocals":
            return len(self.arg_names)
        if name == "co_posonlyargcount":
            return len(self.posonly_names)
        if name == "co_qualname":
            return self.name
        raise AttributeError(name)

    def replace(self, **changes: object) -> "CodeObject":
        """Return a copy using the subset of CPython code fields we model."""
        allowed = {
            key: value for key, value in changes.items()
            if key in self.__dataclass_fields__
        }
        return replace(self, **allowed)

    def validate(self) -> None:
        for offset, instr in enumerate(self.instructions):
            if not isinstance(instr.op, Op):
                raise ValueError(f"{self.name}: invalid opcode at {offset}")
            if instr.op in (Op.LOAD_CONST, Op.MAKE_FUNCTION, Op.MAKE_CLASS, Op.MATCH_EXCEPTION, Op.MATCH_PATTERN) and not 0 <= instr.arg < len(self.constants):
                raise ValueError(f"{self.name}: constant index out of range at {offset}")
            if instr.op in (Op.LOAD_NAME, Op.STORE_NAME, Op.STORE_GLOBAL, Op.GET_ATTR, Op.SET_ATTR, Op.DELETE_ATTR, Op.DELETE_NAME) and not 0 <= instr.arg < len(self.names):
                raise ValueError(f"{self.name}: name index out of range at {offset}")
            if instr.op in (Op.JUMP, Op.JUMP_IF_FALSE, Op.JUMP_IF_TRUE, Op.JUMP_IF_FALSE_KEEP, Op.JUMP_IF_TRUE_KEEP, Op.TRY_BEGIN) and not 0 <= instr.arg <= len(self.instructions):
                raise ValueError(f"{self.name}: jump target out of range at {offset}")
            if instr.arg < 0:
                raise ValueError(f"{self.name}: negative operand at {offset}")
