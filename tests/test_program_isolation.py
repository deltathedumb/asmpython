"""Cross-module (whole-program) extension-state isolation test.

This is NOT a `tests/cases*` glob member -- it needs real multi-file
`program.py` whole-program merging, which `tests/runner.py`'s existing
harness never drives (that harness only ever exercises the single-file
Lexer/Parser/native-CLI path). Run directly:

    python -m tests.test_program_isolation

or via `python -m unittest tests.test_program_isolation`, matching the
`unittest.TestCase` convention already used by `tests/test_pyinbin.py` and
`tests/test_pytest_scout.py`.

Each module `program.py` merges gets its own fresh `Parser` (and therefore
its own fresh `ExtensionContext` -- see `extensions.py`'s module docstring),
so `extend constants` in one file must never leak into another file's parse,
even when one imports the other.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asmpython._compiler import ast_nodes as A
from asmpython._compiler.program import load_program


class ProgramIsolationTests(unittest.TestCase):
    def test_const_activation_does_not_leak_into_imported_module(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "__init__.py").write_text("")
            (root / "helper.py").write_text(
                "const = 99          # ordinary variable name -- constants "
                "NOT active here\nprint(const)\n"
            )
            main_src = (
                "extend constants\n"
                "const SHARED = 1\n"
                "from . import helper\n"
                "print(SHARED)\n"
            )
            main_path = root / "main.py"
            main_path.write_text(main_src)

            mod = load_program(main_src, main_path)

            # Must not raise (proving helper.py's bare `const = 99` parsed as
            # a plain assignment, unaffected by main.py's own `extend
            # constants` -- because helper.py gets its own fresh
            # Parser/ExtensionContext at program.py's second Parser(...)
            # construction site).
            self.assertTrue(
                any(
                    isinstance(s, A.Assign) and s.target == "const"
                    for s in mod.body
                )
            )
            # main.py's own const declaration must still be a real ConstDecl.
            self.assertTrue(
                any(
                    isinstance(s, A.ConstDecl) and s.name == "SHARED"
                    for s in mod.body
                )
            )
            # No leaked Extend/Retract directives in the merged module --
            # they're filtered out per-module before program.py ever merges
            # anything (Parser.parse()'s final filter step).
            self.assertFalse(
                any(isinstance(s, (A.Extend, A.Retract)) for s in mod.body)
            )

    def test_const_without_own_extend_is_silently_dropped_not_raised(self) -> None:
        """helper2.py does `const Y = 5` WITHOUT its own `extend constants` --
        proving activation genuinely does not leak downstream into imported
        modules' own parses.

        IMPORTANT CAVEAT: per program.py's existing exception handling
        around its second Parser(...) construction site (a module that
        fails to parse is silently skipped, not propagated as a
        whole-program failure), the actual observable behavior here is that
        helper2.py is silently DROPPED from the whole-program merge rather
        than load_program raising an exception. This test checks for THAT
        actual behavior, not a raised exception -- a raised exception would
        contradict program.py's own existing, deliberate leniency toward
        modules it can't parse (they may be third-party-ish or use
        constructs outside the supported subset).
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "__init__.py").write_text("")
            (root / "helper2.py").write_text("const Y = 5\nprint(Y)\n")
            main_src = (
                "extend constants\n"
                "const SHARED = 1\n"
                "from . import helper2\n"
                "print(SHARED)\n"
            )
            main_path = root / "main.py"
            main_path.write_text(main_src)

            # Must not raise -- helper2.py is simply dropped from the merge.
            mod = load_program(main_src, main_path)

            self.assertFalse(
                any(
                    isinstance(s, A.ExprStmt)
                    and isinstance(s.expr, A.Call)
                    and s.expr.func == "print"
                    and s.expr.args
                    and isinstance(s.expr.args[0], A.Name)
                    and s.expr.args[0].name == "Y"
                    for s in mod.body
                )
            )


if __name__ == "__main__":
    unittest.main()
