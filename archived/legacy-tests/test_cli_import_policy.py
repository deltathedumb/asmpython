from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asmpython._compiler.cli import cli
from asmpython._compiler.packaging.project import ProjectConfig, load_project, save_project
from asmpython._compiler.packaging.site_packages import SitePackageImportError
from asmpython._compiler.packaging.pypi import (
    PypiError,
    install_pypi_package,
    list_pypi_packages,
    uninstall_pypi_package,
)


class CliImportPolicyTests(unittest.TestCase):
    def test_static_source_forces_native_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "main.py"
            source.write_text("import requests\nprint(1)\n", encoding="utf-8")
            self.assertEqual(
                cli.prepare_argv(["build", str(source)]),
                ["build", str(source), "--no-pyinbin-fallback"],
            )
            self.assertEqual(
                cli.prepare_argv([str(source)]),
                [str(source), "--no-pyinbin-fallback"],
            )

    def test_dynamic_import_source_keeps_interpreter_fallback(self) -> None:
        dynamic_sources = (
            "module = __import__(name)\n",
            "import importlib\nmodule = importlib.import_module(name)\n",
            "from importlib import import_module\nmodule = import_module(name)\n",
            "import imp\nmodule = imp.load_module(name, file, path, desc)\n",
            "import importlib as il\nmodule = il.import_module(name)\n",
            "from importlib import import_module as load\nmodule = load(name)\n",
            "import importlib\nload = importlib.import_module\nmodule = load(name)\n",
            "import importlib\nload = importlib.import_module\nagain = load\nmodule = again(name)\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "main.py"
            for text in dynamic_sources:
                source.write_text(text, encoding="utf-8")
                self.assertEqual(
                    cli.prepare_argv(["build", str(source)]),
                    ["build", str(source)],
                )

    def test_dynamic_import_text_in_comments_and_strings_is_static(self) -> None:
        static_sources = (
            "# importlib.import_module(name)\nprint(1)\n",
            "text = 'import_module(name)'\nprint(text)\n",
            "def import_module(name):\n    return name\nprint(import_module('x'))\n",
        )
        for source in static_sources:
            self.assertFalse(cli.source_uses_dynamic_import(source), source)

    def test_dynamic_import_in_reachable_local_module_keeps_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.py"
            helper = root / "helper.py"
            source.write_text("import helper\nprint(1)\n", encoding="utf-8")
            helper.write_text("module = __import__(name)\n", encoding="utf-8")
            self.assertEqual(
                cli.prepare_argv(["build", str(source)]),
                ["build", str(source)],
            )

    def test_static_project_pyinbin_roots_do_not_force_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "main.py"
            source.write_text("print(1)\n", encoding="utf-8")
            project = root / "project.json"
            save_project(
                ProjectConfig(entry="main.py", pyinbin_imports=["plugins"]),
                project,
            )

            observed: list[list[str]] = []

            def fake_legacy(argv: list[str]) -> int:
                cfg = cli._legacy_cli.load_project(project)
                observed.append(list(cfg.pyinbin_imports))
                return 0

            prepared = cli.prepare_argv(["build", str(project)])
            with mock.patch.object(cli._legacy_cli, "main", fake_legacy):
                self.assertEqual(
                    cli._call_legacy_with_static_project_policy(prepared),
                    0,
                )
            self.assertEqual(observed, [[]])

    def test_eval_and_exec_do_not_enable_import_fallback(self) -> None:
        self.assertFalse(cli.source_uses_dynamic_import("eval(code)\n"))
        self.assertFalse(cli.source_uses_dynamic_import("exec(code)\n"))

    def test_project_entry_controls_fallback_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "app.py"
            source.write_text("print(1)\n", encoding="utf-8")
            project = root / "project.json"
            save_project(ProjectConfig(entry="app.py"), project)
            self.assertEqual(
                cli.prepare_argv(["build", str(project)]),
                ["build", str(project), "--no-pyinbin-fallback"],
            )

    def test_private_pypi_command_is_rejected_before_legacy_dispatch(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(cli._legacy_cli, "main") as legacy:
            with contextlib.redirect_stderr(stderr):
                result = cli.main(["pypi", "install", "requests"])
        self.assertEqual(result, 2)
        legacy.assert_not_called()
        self.assertIn("python -m pip install", stderr.getvalue())

    def test_site_package_resolution_error_is_reported_without_fallback(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(
            cli,
            "prepare_argv",
            side_effect=SitePackageImportError("unsupported extension"),
        ):
            with mock.patch.object(cli._legacy_cli, "main") as legacy:
                with contextlib.redirect_stderr(stderr):
                    result = cli.main(["build", "missing.py"])
        self.assertEqual(result, 1)
        legacy.assert_not_called()
        self.assertIn("native import resolution failed", stderr.getvalue())

    def test_legacy_pypi_api_is_migration_error_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            with self.assertRaises(PypiError):
                install_pypi_package("requests", destination)
            with self.assertRaises(PypiError):
                uninstall_pypi_package("requests", destination)
            with self.assertRaises(PypiError):
                list_pypi_packages(destination)
            self.assertEqual(list(destination.iterdir()), [])

    def test_project_schema_drops_private_pypi_store_fields(self) -> None:
        cfg = ProjectConfig()
        self.assertNotIn("pypi_packages", cfg.to_dict())
        self.assertNotIn("pypi_dir", cfg.to_dict())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "project.json"
            path.write_text(
                json.dumps(
                    {
                        "entry": "main.py",
                        "pypi_packages": ["requests"],
                        "pypi_dir": "pypi_libs",
                    }
                ),
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                loaded = load_project(path)
            self.assertEqual(loaded.pypi_packages, [])
            self.assertEqual(loaded.pypi_dir, "")
            self.assertIn("unknown project field", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
