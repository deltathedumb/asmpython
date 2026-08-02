from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asmpython._compiler import program
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.packaging.site_packages import (
    SitePackageImportError,
    _is_ffi_stdlib,
    install_native_import_resolution,
    install_pyinbin_site_package_resolution,
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

    def test_bundled_stdlib_precedes_same_named_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            entry = project / "main.py"
            source = "import math\nimport pathlib\n"
            entry.write_text(source, encoding="utf-8")
            local_math = project / "math.py"
            local_pathlib = project / "pathlib.py"
            local_math.write_text("shadow = 1\n", encoding="utf-8")
            local_pathlib.write_text("shadow = 1\n", encoding="utf-8")

            module = Parser(Lexer(source).tokenize()).parse()
            imports = {
                path.resolve()
                for path in program._project_imports(module, entry, project)
            }
            self.assertNotIn(local_math.resolve(), imports)
            self.assertNotIn(local_pathlib.resolve(), imports)
            self.assertIn(
                program._resolve_bundled_stdlib("pathlib").resolve(),
                imports,
            )

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
                # site_package_roots() intentionally preserves each root's
                # original form (matching what real sys.path/.pth entries
                # look like) rather than returning its .resolve()d form --
                # .resolve() on Windows can rewrite a long path segment to
                # its legacy 8.3 short-name alias, and that alias must not
                # leak into paths this module hands back to callers who
                # never asked for one (see host_site_packages.py's
                # _append_unique). Assert against the same unresolved form
                # the second assertion below already (correctly) expects,
                # rather than a resolved form that only coincidentally
                # matches when the temp directory's own path has no
                # short-name-eligible segment.
                self.assertIn(editable, site_package_roots())
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

    def test_pyinbin_bundle_miss_falls_back_to_import_roots(self) -> None:
        from asmpython.pyinbin.loader import SourceLoader

        install_pyinbin_site_package_resolution()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / "project"
            site = base / "site-packages"
            project.mkdir()
            site.mkdir()
            module_path = site / "dynamic_pkg.py"
            module_path.write_text("VALUE = 42\n", encoding="utf-8")

            loader = SourceLoader(source_root=project, import_roots=[site])
            # Simulate a verified bundle that simply does not contain this
            # dynamic import. The host resolver must then continue to roots.
            loader.bundle = base / "bundle"
            loader._bundle_modules = {}
            source, filename = loader._source_for("dynamic_pkg")

            self.assertEqual(source, "VALUE = 42\n")
            self.assertEqual(Path(filename), module_path)
            self.assertFalse(loader._is_package("dynamic_pkg"))


if __name__ == "__main__":
    unittest.main()
