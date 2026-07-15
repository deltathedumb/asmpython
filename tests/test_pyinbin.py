"""Unit coverage for the portable pyinbin bootstrap runtime and bundles."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from asmpython._compiler.pyinbin_package import (
    MANIFEST_NAME,
    PyinbinPackageError,
    build_source_bundle,
    verify_source_bundle,
)
from asmpython._compiler.project import load_project
from asmpython.pyinbin import CodeObject, Instruction, Op, VirtualMachine, run_source


class PyinbinVmTests(unittest.TestCase):
    def test_module_function_call_uses_module_namespace(self) -> None:
        add = CodeObject(
            name="add",
            arg_names=["left", "right"],
            names=["left", "right"],
            instructions=[
                Instruction(Op.LOAD_NAME, 0),
                Instruction(Op.LOAD_NAME, 1),
                Instruction(Op.BINARY_ADD),
                Instruction(Op.RETURN),
            ],
        )
        main = CodeObject(
            name="main",
            constants=[add, 20, 22],
            names=["add"],
            instructions=[
                Instruction(Op.LOAD_CONST, 0),
                Instruction(Op.MAKE_FUNCTION, 0),
                Instruction(Op.STORE_NAME, 0),
                Instruction(Op.LOAD_NAME, 0),
                Instruction(Op.LOAD_CONST, 1),
                Instruction(Op.LOAD_CONST, 2),
                Instruction(Op.CALL, 2),
                Instruction(Op.RETURN),
            ],
        )

        namespace: dict[str, object] = {}
        self.assertEqual(VirtualMachine().run(main, namespace), 42)
        self.assertIn("add", namespace)

    def test_loop_and_collections(self) -> None:
        program = CodeObject(
            name="loop",
            constants=[0, 1, 4, "total"],
            names=["index", "total"],
            instructions=[
                Instruction(Op.LOAD_CONST, 0),
                Instruction(Op.STORE_NAME, 0),
                Instruction(Op.LOAD_CONST, 0),
                Instruction(Op.STORE_NAME, 1),
                Instruction(Op.LOAD_NAME, 0),
                Instruction(Op.LOAD_CONST, 2),
                Instruction(Op.COMPARE_LT),
                Instruction(Op.JUMP_IF_FALSE, 17),
                Instruction(Op.LOAD_NAME, 1),
                Instruction(Op.LOAD_NAME, 0),
                Instruction(Op.BINARY_ADD),
                Instruction(Op.STORE_NAME, 1),
                Instruction(Op.LOAD_NAME, 0),
                Instruction(Op.LOAD_CONST, 1),
                Instruction(Op.BINARY_ADD),
                Instruction(Op.STORE_NAME, 0),
                Instruction(Op.JUMP, 4),
                Instruction(Op.LOAD_CONST, 3),
                Instruction(Op.LOAD_NAME, 1),
                Instruction(Op.BUILD_LIST, 2),
                Instruction(Op.RETURN),
            ],
        )

        self.assertEqual(VirtualMachine().run(program), ["total", 6])


class PyinbinBundleTests(unittest.TestCase):
    def test_bundle_is_verified_and_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "plugins"
            package.mkdir()
            (package / "__init__.py").write_text("NAME = 'plugins'\n", encoding="utf-8")
            module = package / "tool.py"
            module.write_text("VALUE = 42\n", encoding="utf-8")
            destination = root / "bundle"

            manifest = build_source_bundle(root, ["plugins"], destination)
            self.assertEqual([item.name for item in manifest], ["plugins", "plugins.tool"])
            self.assertEqual([item.name for item in verify_source_bundle(destination)], ["plugins", "plugins.tool"])

            module_path = destination / "src" / "plugins" / "tool.py"
            module_path.write_text("VALUE = 7\n", encoding="utf-8")
            with self.assertRaises(PyinbinPackageError):
                verify_source_bundle(destination)

            self.assertTrue((destination / MANIFEST_NAME).is_file())

    def test_project_manifest_preserves_pyinbin_import_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project.json"
            project.write_text(
                '{"entry":"main.py","pyinbin_imports":["plugins", "vendor.tools"]}\n',
                encoding="utf-8",
            )
            self.assertEqual(load_project(project).pyinbin_imports, ["plugins", "vendor.tools"])


class PyinbinSourceTests(unittest.TestCase):
    def test_source_execution_routes_import_through_pyinbin_loader(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tool.py").write_text("def twice(value):\n    return value * 2\n", encoding="utf-8")
            entry = root / "main.py"
            entry.write_text("from tool import twice\nprint(twice(21))\n", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "42\n")

    def test_dotted_import_preserves_the_root_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "plugins"
            package.mkdir()
            (package / "__init__.py").write_text("\n", encoding="utf-8")
            (package / "tool.py").write_text("VALUE = 42\n", encoding="utf-8")
            entry = root / "main.py"
            entry.write_text("import plugins.tool\nprint(plugins.tool.VALUE)\n", encoding="utf-8")

            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "42\n")

    def test_iteration_tuples_subscripts_and_augmented_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "main.py"
            entry.write_text(
                "values = [1, 2, 3]\n"
                "total = 0\n"
                "for value in values:\n"
                "    total += value\n"
                "print((total, values[1]))\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "(6, 2)\n")

    def test_classes_methods_and_inheritance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "main.py"
            entry.write_text(
                "class Base:\n"
                "    def __init__(self, value):\n"
                "        self.value = value\n"
                "    def doubled(self):\n"
                "        return self.value * 2\n"
                "class Child(Base):\n"
                "    pass\n"
                "item = Child(21)\n"
                "print(item.doubled())\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "42\n")

    def test_raise_and_typed_exception_handler(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "main.py"
            entry.write_text(
                "try:\n"
                "    raise ValueError('bad value')\n"
                "except ValueError as error:\n"
                "    print(error)\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "bad value\n")

    def test_relative_imports_use_module_package_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "pkg"
            package.mkdir()
            (package / "__init__.py").write_text("from .helper import VALUE\n", encoding="utf-8")
            (package / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
            entry = root / "main.py"
            entry.write_text("import pkg\nprint(pkg.VALUE)\n", encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "42\n")

    def test_global_statement_updates_module_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            entry = Path(temporary) / "main.py"
            entry.write_text(
                "counter = 0\n"
                "def increment():\n"
                "    global counter\n"
                "    counter += 1\n"
                "increment()\n"
                "print(counter)\n",
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                run_source(entry)
            self.assertEqual(output.getvalue(), "1\n")


if __name__ == "__main__":
    unittest.main()
