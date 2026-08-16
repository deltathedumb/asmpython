from __future__ import annotations

import unittest
from pathlib import Path

import asmpython
from asmpython._compiler import program
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser


class SelfHostedEntrypointTests(unittest.TestCase):
    def test_package_entry_merges_legacy_main_but_excludes_host_backend(self) -> None:
        entry = Path(asmpython.__file__).resolve().with_name("__main__.py")
        source = entry.read_text(encoding="utf-8")
        module = Parser(Lexer(source).tokenize()).parse()
        root = program._project_root(entry)

        imports = [path.resolve() for path in program._project_imports(module, entry, root)]
        self.assertIn(
            (entry.parent / "_compiler" / "__main__.py").resolve(),
            imports,
        )
        self.assertFalse(
            any("_backends" in path.parts for path in imports),
            imports,
        )

        merged = program.load_program(source, entry)
        self.assertIn("main", {func.name for func in merged.funcs})


if __name__ == "__main__":
    unittest.main()
