from __future__ import annotations

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
