from __future__ import annotations

import unittest

from asmpython._backends.x86_64.unpack_normalize import (
    install,
    normalize_literal_unpacks,
)
from asmpython._compiler import ast_nodes as A
from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.ssa.ir import F64, PTR
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze


def _parsed_module(source: str) -> A.Module:
    return Parser(Lexer(source).tokenize()).parse()


def _checked_module(source: str) -> A.Module:
    module = _parsed_module(source)
    analyze(module)
    return module


def _tuple_assigns(module: A.Module) -> list[A.TupleAssign]:
    return [
        statement
        for statement in module.body
        if isinstance(statement, A.TupleAssign)
    ]


def _tuple_assign(module: A.Module) -> A.TupleAssign:
    statements = _tuple_assigns(module)
    if not statements:
        raise AssertionError("source did not produce a TupleAssign")
    return statements[0]


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

    def test_typed_tuple_name_becomes_typed_subscripts(self) -> None:
        module = _checked_module(
            'pair = ("a", 1.5)\n'
            'label, ratio = pair\n'
        )
        statement = _tuple_assign(module)

        normalize_literal_unpacks(module)

        self.assertEqual(len(statement.values), 2)
        self.assertTrue(all(isinstance(value, A.Subscript) for value in statement.values))
        self.assertEqual(
            [value.inferred_type for value in statement.values],
            ["str", "float"],
        )
        self.assertTrue(all(value.obj is statement.values[0].obj for value in statement.values))

    def test_string_literal_becomes_character_literals(self) -> None:
        module = _checked_module('a, b, c = "xyz"\n')
        statement = _tuple_assign(module)

        normalize_literal_unpacks(module)

        self.assertEqual(len(statement.values), 3)
        self.assertTrue(all(isinstance(value, A.StrLit) for value in statement.values))
        self.assertEqual([value.value for value in statement.values], ["x", "y", "z"])

    def test_unicode_string_uses_codepoint_literals(self) -> None:
        module = _checked_module('first, second = "éx"\n')
        statement = _tuple_assign(module)
        normalize_literal_unpacks(module)
        self.assertEqual(len(statement.values), 2)
        self.assertEqual([value.value for value in statement.values], ["é", "x"])

    def test_mismatched_literal_arity_is_not_rewritten(self) -> None:
        module = _parsed_module('first, second = "x"\n')
        statement = _tuple_assign(module)
        original_values = list(statement.values)
        normalize_literal_unpacks(module)
        self.assertEqual(statement.values, original_values)

    def test_tuple_call_and_starred_unpack_are_unchanged(self) -> None:
        call_module = _parsed_module('a, b = make_pair()\n')
        call_statement = _tuple_assign(call_module)
        original_call_values = list(call_statement.values)
        normalize_literal_unpacks(call_module)
        self.assertEqual(call_statement.values, original_call_values)

        starred_module = _parsed_module('values = [1, 2, 3]\na, *rest = values\n')
        starred_statement = _tuple_assign(starred_module)
        original_starred_values = list(starred_statement.values)
        normalize_literal_unpacks(starred_module)
        self.assertEqual(starred_statement.values, original_starred_values)

    def test_lowered_literals_keep_pointer_and_float_register_classes(self) -> None:
        install()
        module = _checked_module(
            'a, b, c = "xyz"\n'
            'label, count, ratio = ("ok", 5, 1.5)\n'
            'pair = ("saved", 2.5)\n'
            'saved_label, saved_ratio = pair\n'
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
        self.assertEqual(char_calls, [])

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
