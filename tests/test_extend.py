"""Unit coverage for the public plugin-authoring API (`asmpython/extend.py`):
`Extension`/`Backend`/`Linker`, and the statement-handler dispatch path a
plugin-registered `Extension` actually exercises in the parser.

Run: python -m unittest tests.test_extend
"""

from __future__ import annotations

import unittest

import asmpython
from asmpython._backends import get_backend
from asmpython._compiler import ast_nodes as A
from asmpython._compiler.errors import ParseError
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._linkers import get_linker


def _handle_let(parser: Parser, pos) -> A.Assign:
    """`let NAME = value` -- trivial single-binding statement, used purely
    to exercise the statement-handler contract end to end."""
    name = parser._expect("NAME").value
    parser._expect("OP", "=")
    value = parser._parse_expr()
    parser._expect("NEWLINE")
    return A.Assign(target=name, value=value, pos=pos)


class ExtensionAuthoringTests(unittest.TestCase):
    def test_extension_registers_and_activates_by_id(self) -> None:
        asmpython.Extension(id="test_metadata_only_ext")
        # No statement_handlers -- a pure metadata/dependency marker.
        # Activating it must succeed and not add any grammar.
        mod = Parser(Lexer("x = 1\n").tokenize(), frozenset({"test_metadata_only_ext"})).parse()
        self.assertTrue(any(isinstance(s, A.Assign) and s.target == "x" for s in mod.body))

    def test_statement_handler_dispatches_and_produces_real_ast(self) -> None:
        asmpython.Extension(id="test_let_binding", statement_handlers={"let": _handle_let})
        src = "let y = 42\nprint(y)\n"
        mod = Parser(Lexer(src).tokenize(), frozenset({"test_let_binding"})).parse()
        self.assertTrue(
            any(isinstance(s, A.Assign) and s.target == "y" for s in mod.body)
        )

    def test_claimed_keyword_is_ordinary_identifier_when_extension_inactive(self) -> None:
        # A DIFFERENT plugin, not activated below -- its keyword must not
        # leak into a Parser that never activated it.
        asmpython.Extension(id="test_let_binding_2", statement_handlers={"let2": _handle_let})
        mod = Parser(Lexer("let2 = 5\nprint(let2)\n").tokenize()).parse()
        self.assertTrue(
            any(isinstance(s, A.Assign) and s.target == "let2" for s in mod.body)
        )

    def test_duplicate_extension_id_conflicts_with_registered_keyword(self) -> None:
        # Registering two extensions under the SAME keyword and activating
        # both must raise the same statement-prefix-collision diagnostic
        # the in-tree registry already enforces.
        asmpython.Extension(id="test_dup_a", statement_handlers={"dupkw": _handle_let})
        asmpython.Extension(id="test_dup_b", statement_handlers={"dupkw": _handle_let})
        with self.assertRaises(ParseError):
            Parser(Lexer("x = 1\n").tokenize(), frozenset({"test_dup_a", "test_dup_b"}))


class BackendLinkerAuthoringTests(unittest.TestCase):
    def test_backend_registers_and_is_retrievable(self) -> None:
        class _DummyBackend:
            requested_args: list = []
            default_linker = "gcc"

            def compile(self, module, args):
                return {"x.o": b"stub"}

            def link(self, objects, args):
                return {"output": b"stub-exe"}

        impl = _DummyBackend()
        asmpython.Backend(name="test_dummy_backend", impl=impl)
        self.assertIs(get_backend("test_dummy_backend"), impl)

    def test_linker_registers_and_is_retrievable(self) -> None:
        class _DummyLinker:
            requested_args: list = []

            def link(self, ctx):
                return b"linked"

        impl = _DummyLinker()
        asmpython.Linker(name="test_dummy_linker", impl=impl)
        self.assertIs(get_linker("test_dummy_linker"), impl)


if __name__ == "__main__":
    unittest.main()
