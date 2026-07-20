from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asmpython._compiler.program import load_program


class PackageReexportChainTests(unittest.TestCase):
    def test_nested_entry_merges_reexported_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "somnia"
            package = root / "somnia"
            tests = root / "tests" / "parity"
            package.mkdir(parents=True)
            tests.mkdir(parents=True)

            (root / "pyproject.toml").write_text(
                "[project]\nname = 'somnia-test'\n",
                encoding="utf-8",
            )
            (package / "__init__.py").write_text(
                "from .core import DataModel\n",
                encoding="utf-8",
            )
            (package / "core.py").write_text(
                "class DataModel:\n"
                "    def __init__(self, value: int = 42) -> None:\n"
                "        self.value: int = value\n",
                encoding="utf-8",
            )
            entry = tests / "snapshot.py"
            entry.write_text(
                "from somnia import DataModel\n"
                "model = DataModel()\n"
                "print(model.value)\n",
                encoding="utf-8",
            )

            merged = load_program(entry.read_text(encoding="utf-8"), entry)
            names = [definition.name for definition in merged.classes]
            self.assertIn("DataModel", names)

    def test_dotted_reexport_chain_merges_class(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "engine"
            package = root / "engine"
            model = package / "model"
            entry_dir = root / "examples"
            model.mkdir(parents=True)
            entry_dir.mkdir(parents=True)

            (root / "pyproject.toml").write_text(
                "[project]\nname = 'engine-test'\n",
                encoding="utf-8",
            )
            (package / "__init__.py").write_text(
                "from .model import Scene\n",
                encoding="utf-8",
            )
            (model / "__init__.py").write_text(
                "from .core import Scene\n",
                encoding="utf-8",
            )
            (model / "core.py").write_text(
                "class Scene:\n"
                "    pass\n",
                encoding="utf-8",
            )
            entry = entry_dir / "main.py"
            entry.write_text(
                "from engine import Scene\n"
                "scene = Scene()\n",
                encoding="utf-8",
            )

            merged = load_program(entry.read_text(encoding="utf-8"), entry)
            names = [definition.name for definition in merged.classes]
            self.assertIn("Scene", names)


if __name__ == "__main__":
    unittest.main()
