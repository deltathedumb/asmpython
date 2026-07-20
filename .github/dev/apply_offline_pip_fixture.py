from __future__ import annotations

from pathlib import Path


TEST_SOURCE = '''from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from asmpython._compiler import program
from asmpython._compiler.site_packages import (
    install_native_import_resolution,
    resolve_site_package,
)


class PipInstallIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        install_native_import_resolution()

    def test_pip_installed_pure_python_package_is_native_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "site-packages"
            project = root / "project"
            target.mkdir()
            project.mkdir()

            wheel = root / "asmpython_pip_probe-1.0.0-py3-none-any.whl"
            dist_info = "asmpython_pip_probe-1.0.0.dist-info"
            module_path = "asmpython_pip_probe/__init__.py"
            metadata_path = f"{dist_info}/METADATA"
            wheel_metadata_path = f"{dist_info}/WHEEL"
            record_path = f"{dist_info}/RECORD"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    module_path,
                    "def answer() -> int:\\n    return 42\\n",
                )
                archive.writestr(
                    metadata_path,
                    "Metadata-Version: 2.1\\n"
                    "Name: asmpython-pip-probe\\n"
                    "Version: 1.0.0\\n",
                )
                archive.writestr(
                    wheel_metadata_path,
                    "Wheel-Version: 1.0\\n"
                    "Generator: asmpython-test\\n"
                    "Root-Is-Purelib: true\\n"
                    "Tag: py3-none-any\\n",
                )
                archive.writestr(
                    record_path,
                    f"{module_path},,\\n"
                    f"{metadata_path},,\\n"
                    f"{wheel_metadata_path},,\\n"
                    f"{record_path},,\\n",
                )

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-deps",
                    "--target",
                    str(target),
                    str(wheel),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            entry = project / "main.py"
            source = (
                "from asmpython_pip_probe import answer\\n"
                "print(answer())\\n"
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
'''


def main() -> None:
    Path("tests/test_pip_install_integration.py").write_text(
        TEST_SOURCE,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
