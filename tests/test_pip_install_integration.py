from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from asmpython._compiler import program
from asmpython._compiler.site_packages import resolve_site_package


class PipInstallIntegrationTests(unittest.TestCase):
    def test_pip_installed_pure_python_package_is_native_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package"
            target = root / "site-packages"
            project = root / "project"
            package.mkdir()
            target.mkdir()
            project.mkdir()

            (package / "pyproject.toml").write_text(
                """\
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "asmpython-pip-probe"
version = "1.0.0"
""",
                encoding="utf-8",
            )
            module = package / "asmpython_pip_probe"
            module.mkdir()
            (module / "__init__.py").write_text(
                "def answer() -> int:\n    return 42\n",
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--no-build-isolation",
                    "--target",
                    str(target),
                    str(package),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            entry = project / "main.py"
            source = (
                "from asmpython_pip_probe import answer\n"
                "print(answer())\n"
            )
            entry.write_text(source, encoding="utf-8")
            with mock.patch.object(sys, "path", [str(target)]):
                resolved = resolve_site_package("asmpython_pip_probe")
                merged = program.load_program(source, entry)

            self.assertEqual(
                resolved,
                target / "asmpython_pip_probe" / "__init__.py",
            )
            self.assertIn("answer", {func.name for func in merged.funcs})


if __name__ == "__main__":
    unittest.main()
