from __future__ import annotations

import unittest

from asmpython._compiler import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze


def _lower(source: str):
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    sema_analyze(module)
    return ir_lower.lower_module(module)


class PublicExportReachabilityTests(unittest.TestCase):
    def test_public_function_never_called_from_main_is_still_lowered(self) -> None:
        lowered = _lower(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def add(left: int, right: int) -> int:\n"
            "    return left + right\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        self.assertEqual(lowered.exports, ["add"])
        self.assertIn("add", [f.name for f in lowered.funcs])

    def test_unmarked_function_never_called_is_dropped(self) -> None:
        lowered = _lower(
            "def unused(left: int) -> int:\n"
            "    return left\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        self.assertEqual(lowered.exports, [])
        self.assertNotIn("unused", [f.name for f in lowered.funcs])

    def test_public_class_exports_class_and_every_method(self) -> None:
        lowered = _lower(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "class Widget:\n"
            "    def __init__(self, value: int) -> None:\n"
            "        self.value = value\n"
            "\n"
            "    def get(self) -> int:\n"
            "        return self.value\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        self.assertIn("Widget__get", lowered.exports)
        self.assertIn("Widget__get", [f.name for f in lowered.funcs])

    def test_public_method_on_non_public_class_exports_only_that_method(self) -> None:
        lowered = _lower(
            "from asmpython import Public, access\n"
            "\n"
            "class Widget:\n"
            "    @access(Public)\n"
            "    def get(self) -> int:\n"
            "        return 1\n"
            "\n"
            "    def hidden(self) -> int:\n"
            "        return 2\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        self.assertEqual(lowered.exports, ["Widget__get"])
        self.assertNotIn("Widget__hidden", [f.name for f in lowered.funcs])


if __name__ == "__main__":
    unittest.main()
