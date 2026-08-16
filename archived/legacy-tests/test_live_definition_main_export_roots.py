from __future__ import annotations

import unittest

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze


def _analyze(source: str):
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
    return module


class LiveDefinitionMainExportRootsTests(unittest.TestCase):
    def test_main_with_no_module_level_call_is_not_neutralized(self) -> None:
        # Regression: a program whose only top-level statement is `def
        # main(): ...` (no `if __name__ == "__main__": main()` guard) had
        # ITS OWN ENTRY POINT stubbed to `return 0` by the live-definition
        # dead-code pass, since nothing in mod.body ever calls "main" by
        # name. Every asmpython-compiled executable without that guard
        # silently exited 0 regardless of main's real logic.
        module = _analyze(
            "def main() -> int:\n"
            "    return 5\n"
        )
        main_fn = next(f for f in module.funcs if f.name == "main")
        self.assertEqual(main_fn.body[0].value.value, 5)

    def test_helper_only_reachable_through_main_is_not_neutralized(self) -> None:
        module = _analyze(
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n"
            "\n"
            "def main() -> int:\n"
            "    return add(19, 23)\n"
        )
        add_fn = next(f for f in module.funcs if f.name == "add")
        self.assertIsInstance(add_fn.body[0].value, type(add_fn.body[0].value))
        self.assertFalse(getattr(add_fn, "_dead_body_neutralized", False))

    def test_genuinely_unused_function_is_still_neutralized(self) -> None:
        # The pass's actual job (dead-code stubbing for merged-but-unused
        # stdlib functions) must keep working -- this isn't a blanket
        # disable, just adding main/public-export as extra roots.
        module = _analyze(
            "def unused(left: int) -> int:\n"
            "    return left\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        unused_fn = next(f for f in module.funcs if f.name == "unused")
        self.assertTrue(getattr(unused_fn, "_dead_body_neutralized", False))

    def test_public_export_function_never_called_is_not_neutralized(self) -> None:
        module = _analyze(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        add_fn = next(f for f in module.funcs if f.name == "add")
        self.assertFalse(getattr(add_fn, "_dead_body_neutralized", False))


if __name__ == "__main__":
    unittest.main()
