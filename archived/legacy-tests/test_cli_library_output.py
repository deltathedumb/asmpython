from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SOURCE = (
    "from asmpython import Public, access\n"
    "\n"
    "@access(Public)\n"
    "def add(left: int, right: int) -> int:\n"
    "    return left + right\n"
)


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class CliLibraryOutputTests(unittest.TestCase):
    def test_build_type_library_via_cli_produces_a_working_dll(self) -> None:
        # End-to-end: the real `asmpython build ... --type library` CLI
        # path (not a hand-assembled pipeline) all the way to a loadable,
        # correctly-computing DLL.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "api.py"
            source_path.write_text(_SOURCE, encoding="utf-8")
            dll_path = tmp_path / "api.dll"

            result = subprocess.run(
                [
                    sys.executable, "-m", "asmpython", "build", str(source_path),
                    "--target", "windows", "--backend", "x86-64", "--type", "library",
                    "-o", str(dll_path),
                ],
                capture_output=True, timeout=60,
            )
            self.assertEqual(
                result.returncode, 0,
                f"stdout={result.stdout!r} stderr={result.stderr!r}",
            )
            self.assertTrue(dll_path.is_file())

            library = ctypes.WinDLL(str(dll_path))
            try:
                library.add.restype = ctypes.c_int64
                library.add.argtypes = [ctypes.c_int64, ctypes.c_int64]
                self.assertEqual(library.add(19, 23), 42)
            finally:
                ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
                ctypes.windll.kernel32.FreeLibrary(library._handle)


if __name__ == "__main__":
    unittest.main()
