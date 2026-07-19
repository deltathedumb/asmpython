from __future__ import annotations

import unittest

from asmpython._backends.x86_64.unpack_normalize import (
    install,
    normalize_literal_unpacks,
)
from asmpython._compiler import ast_nodes as A
from asmpython._compiler import ir_lower
from asmpython._compiler.ir import F64, PTR
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def _checked_module(source: str) -> A.Module:
    module = Parser(Lexer(source).tokenize()).parse()
    analyze(module)
    return module


def _tuple_assign(module: A.Module) -> A.TupleAssign:
    for statement in module.body:
        if isinstance(statement, A.TupleAssign):
            return statement
    raise AssertionError("source did not produce a TupleAssign")


def _instructions(ir_module):
    for function in ir_module.funcs:
        for block in function.blocks:
            for instruction in block.instrs:
                yield instruction


class X86LiteralUnpackNormalizeTests(unittest.TestCase):
    def test_tuple_literal_becomes_parallel_typed_values(self) -> None:
        module = _checked_module('label, value, ratio = ("a", 5, 1.5)\n')
        statement = _tuple_assign(module)

        normalize_literal_unpacks(module)

        self.assertEqual(len(statement.values), 3)
        self.assertIsInstance(statement.values[0], A.StrLit)
        self.assertIsInstance(statement.values[1], A.IntLit)
        self.assertIsInstance(statement.values[2], A.FloatLit)

    def test_string_literal_becomes_typed_character_subscripts(self) -> None:
        module = _checked_module('a, b, c = "xyz"\n')
        statement = _tuple_assign(module)

        normalize_literal_unpacks(module)

        self.assertEqual(len(statement.values), 3)
        first_source = statement.values[0].obj
        for index, value in enumerate(statement.values):
            self.assertIsInstance(value, A.Subscript)
            self.assertIs(value.obj, first_source)
            self.assertEqual(value.index.value, index)
            self.assertEqual(value.inferred_type, "str")

    def test_nonliteral_and_starred_unpack_are_unchanged(self) -> None:
        tuple_module = _checked_module('pair = (1, 2)\na, b = pair\n')
        tuple_statement = _tuple_assign(tuple_module)
        original_tuple_values = list(tuple_statement.values)
        normalize_literal_unpacks(tuple_module)
        self.assertEqual(tuple_statement.values, original_tuple_values)

        starred_module = _checked_module('values = [1, 2, 3]\na, *rest = values\n')
        starred_statement = _tuple_assign(starred_module)
        original_starred_values = list(starred_statement.values)
        normalize_literal_unpacks(starred_module)
        self.assertEqual(starred_statement.values, original_starred_values)

    def test_lowered_literals_keep_pointer_and_float_register_classes(self) -> None:
        install()
        module = _checked_module(
            'a, b, c = "xyz"\n'
            'label, count, ratio = ("ok", 5, 1.5)\n'
        )
        lowered = ir_lower.lower_module(module)
        instructions = list(_instructions(lowered))

        char_calls = [
            instruction
            for instruction in instructions
            if instruction.op == "call"
            and instruction.operands
            and instruction.operands[0] == "_abi_str_char_at"
        ]
        self.assertEqual(len(char_calls), 3)
        self.assertTrue(
            all(call.result is not None and call.result.type is PTR for call in char_calls)
        )

        stored_types = {
            instruction.operands[0].type
            for instruction in instructions
            if instruction.op == "store"
            and instruction.operands
            and hasattr(instruction.operands[0], "type")
        }
        self.assertIn(PTR, stored_types)
        self.assertIn(F64, stored_types)

    def test_install_is_idempotent(self) -> None:
        install()
        first = ir_lower.lower_module
        install()
        self.assertIs(ir_lower.lower_module, first)


if __name__ == "__main__":
    unittest.main()
