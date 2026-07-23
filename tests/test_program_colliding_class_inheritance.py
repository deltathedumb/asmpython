from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asmpython._compiler.program import load_program
from asmpython._compiler.sema import analyze


class CollidingClassInheritanceTests(unittest.TestCase):
    def test_module_qualified_same_named_class_chain_keeps_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / "main.py"
            entry.write_text(
                "import leaf\n"
                "item = leaf.Layer()\n",
                encoding="utf-8",
            )
            (root / "leaf.py").write_text(
                "import middle\n"
                "class Layer(middle.Layer):\n"
                "    def previous(self):\n"
                "        return middle.Layer()\n",
                encoding="utf-8",
            )
            (root / "middle.py").write_text(
                "import base\n"
                "class Layer(base.Layer):\n"
                "    def clone(self):\n"
                "        return Layer()\n",
                encoding="utf-8",
            )
            (root / "base.py").write_text(
                "class Layer:\n"
                "    pass\n",
                encoding="utf-8",
            )

            module = load_program(entry.read_text(encoding="utf-8"), entry)
            layers = [owner for owner in module.classes if "Layer" in owner.name]

            self.assertEqual(len(layers), 3)
            self.assertEqual(len({owner.name for owner in layers}), 3)
            by_name = {owner.name: owner for owner in layers}
            leaf = by_name["Layer"]
            middle = by_name[leaf.parent]
            base = by_name[middle.parent]
            self.assertIsNone(base.parent)
            middle_clone = middle.methods[0].body[0].value
            leaf_previous = leaf.methods[0].body[0].value
            self.assertEqual(middle_clone.func, middle.name)
            self.assertEqual(leaf_previous.method, middle.name)

            # This is the original failing stage: the flat class table used to
            # contain only ``Layer(parent=Layer)`` and report a false cycle.
            analyze(module, source_dir=root)


if __name__ == "__main__":
    unittest.main()
