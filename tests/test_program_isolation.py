"""Cross-module (whole-program) extension-activation test.

This is NOT a `tests/cases*` glob member -- it needs real multi-file
`program.py` whole-program merging, which `tests/runner.py`'s existing
harness never drives (that harness only ever exercises the single-file
Lexer/Parser/native-CLI path). Run directly:

    python -m tests.test_program_isolation

or via `python -m unittest tests.test_program_isolation`, matching the
`unittest.TestCase` convention already used by `tests/test_pyinbin.py` and
`tests/test_pytest_scout.py`.

Extension activation is now driven entirely by the `--ext` CLI flag (never
by in-source directives -- a program's grammar never changes without the
invoker's explicit, outside-the-source opt-in). `load_program`'s
`active_extensions` parameter is applied *uniformly* to every module a
whole-program compile merges (see `program.py`'s two `Parser(...)`
construction sites), so there is no more per-file variance to test for --
these tests instead confirm that uniform activation actually reaches every
merged module (an imported module that needs `constants` parses instead of
being silently dropped), and that leaving it off leaves `const` a plain
identifier and makes any real `const` declaration a hard parse error, in
the entry module exactly like every other module.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asmpython._compiler import ast_nodes as A
from asmpython._compiler.errors import ParseError
from asmpython._compiler.program import load_program


class ProgramIsolationTests(unittest.TestCase):
    def test_active_extensions_reach_every_merged_module(self) -> None:
        """helper.py's own `const Y = 5` only parses successfully (rather
        than being silently dropped from the merge, per program.py's
        existing leniency toward modules it can't parse) when `constants`
        is active for the whole compile -- proving activation reaches
        imported modules, not just the entry module."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "__init__.py").write_text("")
            (root / "helper.py").write_text("const Y = 5\ndef use_y():\n    return Y\n")
            main_src = (
                "const SHARED = 1\n"
                "from . import helper\n"
                "print(SHARED)\n"
            )
            main_path = root / "main.py"
            main_path.write_text(main_src)

            mod = load_program(main_src, main_path, active_extensions=frozenset({"constants"}))

            # main.py's own const declaration parsed as a real ConstDecl.
            self.assertTrue(
                any(
                    isinstance(s, A.ConstDecl) and s.name == "SHARED"
                    for s in mod.body
                )
            )
            # helper.py's use_y() function was merged in at all -- proving
            # helper.py parsed successfully (a module that fails to parse
            # is silently skipped entirely, including its funcs).
            self.assertTrue(any(f.name == "use_y" for f in mod.funcs))

    def test_extensions_off_by_default_and_uniform_across_entry_and_imports(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "__init__.py").write_text("")
            (root / "helper.py").write_text(
                "const = 99          # ordinary variable name -- constants "
                "is not active anywhere in this compile\nprint(const)\n"
            )
            main_src = "const = 1\nfrom . import helper\nprint(const)\n"
            main_path = root / "main.py"
            main_path.write_text(main_src)

            # No active_extensions passed -- default is none active.
            mod = load_program(main_src, main_path)

            # main.py's own `const = 1` must have parsed as a plain
            # assignment, not a ConstDecl, since constants was never
            # activated for this compile.
            self.assertTrue(
                any(
                    isinstance(s, A.Assign) and s.target == "const"
                    for s in mod.body
                )
            )
            self.assertFalse(any(isinstance(s, A.ConstDecl) for s in mod.body))

    def test_real_const_declaration_without_activation_is_a_hard_parse_error(self) -> None:
        """A real `const NAME = value` shape in the entry module, with no
        `--ext constants`, is a real ParseError (P_CONST_WITHOUT_EXTENSION)
        that propagates -- unlike an imported module's parse failure, the
        entry module's own parse failure is never silently swallowed."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            main_src = "const SHARED = 1\nprint(SHARED)\n"
            main_path = root / "main.py"
            main_path.write_text(main_src)

            with self.assertRaises(ParseError):
                load_program(main_src, main_path)


if __name__ == "__main__":
    unittest.main()
