from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asmpython._compiler import program


class AbsoluteProjectResolutionTests(unittest.TestCase):
    def test_workspace_named_like_contained_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "somnia"
            package = workspace / "somnia"
            package.mkdir(parents=True)
            (workspace / "pyproject.toml").write_text("[project]\nname='x'\n")
            initializer = package / "__init__.py"
            initializer.write_text("VALUE = 1\n")

            resolved = program._resolve_absolute("somnia", workspace)
            self.assertEqual(resolved, initializer)

    def test_package_directory_can_be_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "somnia"
            package.mkdir()
            initializer = package / "__init__.py"
            initializer.write_text("VALUE = 1\n")

            resolved = program._resolve_absolute("somnia", package)
            self.assertEqual(resolved, initializer)

    def test_dotted_import_below_workspace_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "somnia"
            module = workspace / "somnia" / "model"
            module.mkdir(parents=True)
            (workspace / "pyproject.toml").write_text("[project]\nname='x'\n")
            initializer = module / "__init__.py"
            initializer.write_text("VALUE = 1\n")

            resolved = program._resolve_absolute("somnia.model", workspace)
            self.assertEqual(resolved, initializer)


if __name__ == "__main__":
    unittest.main()
