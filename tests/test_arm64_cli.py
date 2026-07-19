from __future__ import annotations

import io
import stat
import struct
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import asmpython._backends.arm64.__main__ as cli
from asmpython._backends.arm64.codegen import FuncCode, R_AARCH64_CALL26
from asmpython._backends.arm64.elf import build_elf
from asmpython._backends.arm64.linux_link import LinuxArm64Toolchain


_SIMPLE_SOURCE = """\
def main() -> int:
    return 42
"""
_PRINT_SOURCE = """\
def main() -> int:
    print(42)
    return 0
"""


class Arm64CliTests(unittest.TestCase):
    def test_object_command_writes_aarch64_rel_object(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "program.py"
            output = root / "program.o"
            source.write_text(_SIMPLE_SOURCE, encoding="utf-8")

            stream = io.StringIO()
            with redirect_stdout(stream):
                result = cli.main(["object", str(source), "-o", str(output)])

            self.assertEqual(result, 0)
            blob = output.read_bytes()
            ident, elf_type, machine = struct.unpack_from("<16sHH", blob, 0)
            self.assertEqual(ident[:4], b"\x7fELF")
            self.assertEqual(elf_type, 1)
            self.assertEqual(machine, 183)
            self.assertIn("wrote AArch64 object", stream.getvalue())

    def test_requirements_reports_current_print_runtime_compatibility(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "print.py"
            source.write_text(_PRINT_SOURCE, encoding="utf-8")

            stream = io.StringIO()
            with redirect_stdout(stream):
                result = cli.main(["requirements", str(source)])

            report = stream.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("_abi_int_to_base [available]", report)
            self.assertIn("printf [available]", report)
            self.assertIn("compatibility: compatible", report)

    def test_requirements_without_runtime_reports_missing_symbols(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "print.py"
            source.write_text(_PRINT_SOURCE, encoding="utf-8")

            stream = io.StringIO()
            with redirect_stdout(stream):
                result = cli.main(
                    ["requirements", str(source), "--no-runtime"]
                )

            report = stream.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("printf [missing]", report)
            self.assertIn("unsupported by selected runtime", report)

    def test_unsupported_runtime_fails_before_tool_discovery(self) -> None:
        # Keep this symbol deliberately outside RUNTIME_EXPORTS. _abi_new_list
        # used to serve this role, but became supported with the first ARM64
        # list-runtime slice.
        unsupported_symbol = "_abi_list_slice"
        unsupported = build_elf(
            [
                FuncCode(
                    "main",
                    bytes(4),
                    [(0, unsupported_symbol, R_AARCH64_CALL26)],
                )
            ]
        )
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "list_program.py"
            source.write_text("ignored", encoding="utf-8")
            errors = io.StringIO()
            with (
                patch.object(cli, "_compile", return_value=("ignored", unsupported)),
                patch.object(cli, "discover_toolchain") as discover,
                redirect_stderr(errors),
            ):
                result = cli.main(["build", str(source)])

            self.assertEqual(result, 1)
            self.assertIn(unsupported_symbol, errors.getvalue())
            discover.assert_not_called()

    def test_build_command_uses_reusable_builder_and_marks_executable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "program.py"
            output = root / "program-arm64"
            source.write_text(_SIMPLE_SOURCE, encoding="utf-8")
            toolchain = LinuxArm64Toolchain("as", "ld", False)

            stream = io.StringIO()
            with (
                patch.object(cli, "discover_toolchain", return_value=toolchain),
                patch.object(
                    cli,
                    "build_executable_from_object",
                    return_value=b"\x7fELF-executable",
                ) as build,
                redirect_stdout(stream),
            ):
                result = cli.main(
                    [
                        "build",
                        str(source),
                        "-o",
                        str(output),
                        "--no-runtime",
                    ]
                )

            self.assertEqual(result, 0)
            self.assertEqual(output.read_bytes(), b"\x7fELF-executable")
            self.assertTrue(output.stat().st_mode & stat.S_IXUSR)
            build.assert_called_once()
            self.assertFalse(build.call_args.kwargs["include_runtime"])
            self.assertIn("cross toolchain", stream.getvalue())

    def test_compile_error_is_formatted_without_traceback(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "broken.py"
            source.write_text("def main(:\n", encoding="utf-8")
            errors = io.StringIO()

            with redirect_stderr(errors):
                result = cli.main(["object", str(source)])

            diagnostic = errors.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("error:", diagnostic)
            self.assertNotIn("Traceback", diagnostic)


if __name__ == "__main__":
    unittest.main()
