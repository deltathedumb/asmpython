from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asmpython._compiler import program
from asmpython._compiler.site_packages import (
    SitePackageImportError,
    _is_ffi_stdlib,
    install_native_import_resolution,
    resolve_site_package,
    site_package_roots,
)
from asmpython.stdlib import STDLIB_BINDINGS


class SitePackageResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        install_native_import_resolution()

    def test_ffi_stdlib_mirror_covers_live_registry(self) -> None:
        for module in STDLIB_BINDINGS:
            self.assertTrue(_is_ffi_stdlib(module), module)

    def test_resolves_pure_python_module_and_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site-packages"
            site.mkdir()
            (site / "plainmod.py").write_text("VALUE = 7\n", encoding="utf-8")
            package = site / "demo"
            package.mkdir()
            (package / "__init__.py").write_text("VALUE = 8\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [str(site)]):
                self.assertEqual(resolve_site_package("plainmod"), site / "plainmod.py")
                self.assertEqual(resolve_site_package("demo"), package / "__init__.py")

    def test_bundled_and_ffi_stdlib_precede_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site-packages"
            site.mkdir()
            (site / "math.py").write_text("shadow = 1\n", encoding="utf-8")
            (site / "pathlib.py").write_text("shadow = 1\n", encoding="utf-8")
            shadow_pkg = site / "pathlib"
            shadow_pkg.mkdir()
            (shadow_pkg / "shadow.py").write_text("VALUE = 1\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [str(site)]):
                self.assertIsNone(resolve_site_package("math"))
                self.assertIsNone(resolve_site_package("pathlib"))
                self.assertIsNone(resolve_site_package("pathlib.shadow"))

    def test_pth_editable_root_is_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            site = base / "site-packages"
            editable = base / "editable-src"
            site.mkdir()
            editable.mkdir()
            (site / "editable.pth").write_text(str(editable) + "\n", encoding="utf-8")
            (editable / "editable_mod.py").write_text("VALUE = 9\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [str(site)]):
                self.assertIn(editable.resolve(), site_package_roots())
                self.assertEqual(
                    resolve_site_package("editable_mod"),
                    editable / "editable_mod.py",
                )

    def test_native_extension_is_rejected_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site-packages"
            site.mkdir()
            extension = site / "native.cpython-314-x86_64-linux-gnu.so"
            extension.write_bytes(b"not an actual extension")
            with mock.patch.object(sys, "path", [str(site)]):
                with self.assertRaisesRegex(
                    SitePackageImportError,
                    "CPython extension",
                ):
                    resolve_site_package("native")

    def test_unsupported_site_source_fails_before_silent_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            site = Path(tmp) / "site-packages"
            site.mkdir()
            (site / "broken.py").write_text("def broken(:\n", encoding="utf-8")
            with mock.patch.object(sys, "path", [str(site)]):
                with self.assertRaisesRegex(
                    SitePackageImportError,
                    "cannot be compiled natively",
                ):
                    resolve_site_package("broken")

    def test_whole_program_loader_merges_site_package_relative_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            site = base / "site-packages"
            project.mkdir()
            site.mkdir()
            entry = project / "main.py"
            entry_source = "from demo import answer\nprint(answer())\n"
            entry.write_text(entry_source, encoding="utf-8")
            package = site / "demo"
            package.mkdir()
            (package / "__init__.py").write_text(
                "from .core import answer\n",
                encoding="utf-8",
            )
            (package / "core.py").write_text(
                "def answer() -> int:\n    return 42\n",
                encoding="utf-8",
            )
            with mock.patch.object(sys, "path", [str(site)]):
                module = program.load_program(entry_source, entry)
            self.assertIn("answer", {func.name for func in module.funcs})


if __name__ == "__main__":
    unittest.main()
