from __future__ import annotations

import unittest
from unittest import mock

from asmpython._compiler import driver


class DriverRunShellTests(unittest.TestCase):
    def test_string_command_uses_shell_for_cpython_and_selfhost_parity(self) -> None:
        completed = mock.Mock(stdout="", stderr="", returncode=0)
        with mock.patch.object(driver.subprocess, "run", return_value=completed) as run:
            driver._run(["/tool path/nasm", "-f", "elf64", "input file.asm"])
        run.assert_called_once_with(
            '"/tool path/nasm" -f elf64 "input file.asm"',
            capture_output=True,
            text=True,
            shell=True,
        )


if __name__ == "__main__":
    unittest.main()
