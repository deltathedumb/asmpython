from __future__ import annotations

from pathlib import Path


HELPER_INSERTION = '''    return module


def _run_backend_x86_64(
'''

HELPER = '''    return module


def _write_backend_output(
    out_path: Path,
    out_bytes: bytes,
    *,
    executable: bool,
) -> Path:
    """Write backend output and preserve the executable contract on POSIX.

    ``Path.write_bytes`` creates a regular data file, normally mode 0o644.
    Built-in linkers return bytes instead of writing the final path, so Linux
    executables must have their execute bits restored explicitly.
    """
    resolved = out_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_bytes(out_bytes)
    if executable and os.name != "nt":
        resolved.chmod(resolved.stat().st_mode | 0o111)
    return resolved


def _run_backend_x86_64(
'''

OLD_WRITE = '''    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)

    print(f"wrote {out_path}")
'''

NEW_WRITE = '''    out_path = _write_backend_output(
        out_path,
        out_bytes,
        executable=target == "linux",
    )

    print(f"wrote {out_path}")
'''

TEST_SOURCE = '''from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from asmpython._compiler.driver import _write_backend_output


@unittest.skipIf(os.name == "nt", "POSIX execute bits are not meaningful on Windows")
class DriverOutputPermissionTests(unittest.TestCase):
    def test_executable_output_adds_all_execute_bits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "program"
            output.write_bytes(b"old")
            output.chmod(0o600)

            result = _write_backend_output(output, b"ELF", executable=True)

            self.assertEqual(result, output.resolve())
            self.assertEqual(output.read_bytes(), b"ELF")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o711)

    def test_data_output_preserves_non_executable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "data.bin"
            output.write_bytes(b"old")
            output.chmod(0o600)

            _write_backend_output(output, b"data", executable=False)

            self.assertEqual(output.read_bytes(), b"data")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
'''


def main() -> None:
    driver = Path("asmpython/_compiler/driver.py")
    text = driver.read_text(encoding="utf-8")
    if "def _write_backend_output(" not in text:
        if HELPER_INSERTION not in text:
            raise RuntimeError("driver helper insertion point changed")
        text = text.replace(HELPER_INSERTION, HELPER, 1)
    if OLD_WRITE in text:
        text = text.replace(OLD_WRITE, NEW_WRITE, 1)
    elif NEW_WRITE not in text:
        raise RuntimeError("x86-64 output write point changed")
    driver.write_text(text, encoding="utf-8")

    Path("tests/test_driver_output_permissions.py").write_text(
        TEST_SOURCE,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
