"""Unit coverage for the compiler-extension registry (`_compiler/extensions.py`).

These tests exercise `ExtensionContext`'s activation/retraction/dependency/
conflict machinery directly with small dummy `CompilerExtension` subclasses,
rather than through compiled `.py` source -- the only real built-in extension
(`constants`) has no dependents/conflicts of its own to exercise this logic
against, so dummy extensions give direct, isolated coverage of the reusable
registry mechanics themselves.
"""

from __future__ import annotations

import unittest

from asmpython._compiler.errors import ErrorCode
from asmpython._compiler.extensions import (
    CompilerExtension,
    ExtensionContext,
    ExtensionError,
    register_extension,
)


class FakeA(CompilerExtension):
    name = "fake_a"
    version = "1.0"
    requires: dict = {}
    conflicts: set = set()

    def statement_handlers(self) -> dict:
        return {"using_a": "handle_a"}

    def handle_a(self, parser, pos):
        """Real dummy handler so `ExtensionContext.handler_for` -- which
        resolves a string handler name into an actual bound-method
        callable -- has something genuine to resolve against, instead of
        the string sitting unresolved."""
        raise NotImplementedError("dummy handler, never actually invoked by these tests")


class FakeBRequiresA(CompilerExtension):
    name = "fake_b"
    version = "1.0"
    requires = {"fake_a": "1.0"}
    conflicts: set = set()


class FakeConflictsWithA(CompilerExtension):
    name = "fake_conflict"
    version = "1.0"
    requires: dict = {}
    conflicts = {"fake_a"}


class FakeDuplicateStmtPrefix(CompilerExtension):
    name = "fake_dup_stmt"
    version = "1.0"
    requires: dict = {}
    conflicts: set = set()

    def statement_handlers(self) -> dict:
        return {"using_a": "handle_dup"}  # collides with FakeA's "using_a"


class FakeRequiresNewerVersion(CompilerExtension):
    name = "fake_needs_newer"
    version = "1.0"
    requires = {"fake_a": "9.0"}
    conflicts: set = set()


def _register_fakes() -> None:
    for cls in (
        FakeA,
        FakeBRequiresA,
        FakeConflictsWithA,
        FakeDuplicateStmtPrefix,
        FakeRequiresNewerVersion,
    ):
        register_extension(cls)


_register_fakes()


class ActivationTests(unittest.TestCase):
    def test_activate_unknown_extension_raises(self) -> None:
        ctx = ExtensionContext()
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("not_a_real_extension")
        self.assertEqual(cm.exception.code, ErrorCode.P_UNKNOWN_EXTENSION)
        self.assertFalse(ctx.is_active("not_a_real_extension"))

    def test_activate_then_is_active(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        self.assertTrue(ctx.is_active("fake_a"))
        # handler_for resolves the string handler name into a real bound
        # method -- check identity against the active instance's own
        # handle_a, not a raw (name, "handle_a") string pair.
        ext_name, handler = ctx.handler_for("using_a")
        self.assertEqual(ext_name, "fake_a")
        self.assertEqual(handler, ctx._active["fake_a"].handle_a)

    def test_duplicate_activation_raises_and_leaves_state_unchanged(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("fake_a")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_ALREADY_ACTIVE)
        self.assertTrue(ctx.is_active("fake_a"))  # still active, exactly once

    def test_missing_dependency_blocks_activation(self) -> None:
        ctx = ExtensionContext()
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("fake_b")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_MISSING_DEP)
        self.assertFalse(ctx.is_active("fake_b"))

    def test_dependency_loaded_first_allows_activation(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        ctx.activate("fake_b")
        self.assertTrue(ctx.is_active("fake_b"))

    def test_dependency_version_mismatch_blocks_activation(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("fake_needs_newer")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_MISSING_DEP)
        self.assertFalse(ctx.is_active("fake_needs_newer"))

    def test_conflict_blocks_activation_and_leaves_state_unchanged(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("fake_conflict")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_CONFLICT)
        self.assertFalse(ctx.is_active("fake_conflict"))
        self.assertTrue(ctx.is_active("fake_a"))

    def test_conflict_checked_symmetrically(self) -> None:
        # Activate the conflicting extension first; activating fake_a next
        # must still be blocked even though fake_a itself declares no
        # conflicts -- conflicts are checked from both directions.
        ctx = ExtensionContext()
        ctx.activate("fake_conflict")
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("fake_a")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_CONFLICT)
        self.assertFalse(ctx.is_active("fake_a"))

    def test_duplicate_statement_prefix_blocks_activation(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        with self.assertRaises(ExtensionError) as cm:
            ctx.activate("fake_dup_stmt")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_CONFLICT)
        self.assertFalse(ctx.is_active("fake_dup_stmt"))
        # fake_a's own handler must remain untouched.
        ext_name, handler = ctx.handler_for("using_a")
        self.assertEqual(ext_name, "fake_a")
        self.assertEqual(handler, ctx._active["fake_a"].handle_a)


class RetractionTests(unittest.TestCase):
    def test_retract_inactive_raises(self) -> None:
        ctx = ExtensionContext()
        with self.assertRaises(ExtensionError) as cm:
            ctx.retract("fake_a")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_NOT_ACTIVE)

    def test_activate_then_retract_clears_state(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        ctx.retract("fake_a")
        self.assertFalse(ctx.is_active("fake_a"))
        self.assertIsNone(ctx.handler_for("using_a"))

    def test_retract_blocked_by_dependent(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        ctx.activate("fake_b")
        with self.assertRaises(ExtensionError) as cm:
            ctx.retract("fake_a")
        self.assertEqual(cm.exception.code, ErrorCode.P_EXTENSION_RETRACT_BLOCKED)
        self.assertTrue(ctx.is_active("fake_a"))  # unchanged

    def test_retract_dependent_then_dependency_succeeds(self) -> None:
        ctx = ExtensionContext()
        ctx.activate("fake_a")
        ctx.activate("fake_b")
        ctx.retract("fake_b")
        ctx.retract("fake_a")  # no longer blocked
        self.assertFalse(ctx.is_active("fake_a"))
        self.assertFalse(ctx.is_active("fake_b"))


class IsolationTests(unittest.TestCase):
    def test_two_contexts_are_independent(self) -> None:
        ctx1 = ExtensionContext()
        ctx2 = ExtensionContext()
        ctx1.activate("fake_a")
        self.assertTrue(ctx1.is_active("fake_a"))
        self.assertFalse(ctx2.is_active("fake_a"))


class ParserIsolationTests(unittest.TestCase):
    """Two Parser instances constructed back-to-back in the same process
    must never share extension state -- this is the property program.py's
    per-module fresh-Parser construction (and any long-lived CLI process
    compiling multiple files) actually depends on."""

    def test_parser_instances_have_isolated_extension_state(self) -> None:
        from asmpython._compiler.lexer import Lexer
        from asmpython._compiler.parser import Parser

        # Activation now happens via the `--ext` CLI flag (passed into
        # Parser.__init__ as `active_extensions`), not in-source directives
        # -- so isolation here means "activating constants for mod_a's
        # Parser instance must not leak into mod_b's separately-constructed
        # instance", the same property program.py's per-module fresh-Parser
        # construction depends on.
        src_a = "const X = 1\n"
        src_b = "const = 5\nprint(const)\n"

        mod_a = Parser(Lexer(src_a).tokenize(), frozenset({"constants"})).parse()
        mod_b = Parser(Lexer(src_b).tokenize()).parse()

        # mod_a's constants activation must not leak into mod_b's parse: its
        # `const = 5` must have parsed as a plain ordinary assignment.
        from asmpython._compiler import ast_nodes as A

        self.assertTrue(
            any(
                isinstance(s, A.Assign) and s.target == "const"
                for s in mod_b.body
            )
        )
        # mod_a's own `const X = 1` must have parsed as a real ConstDecl.
        self.assertTrue(
            any(isinstance(s, A.ConstDecl) and s.name == "X" for s in mod_a.body)
        )


if __name__ == "__main__":
    unittest.main()
