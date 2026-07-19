from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from asmpython._backends.arm64 import linux_link
from asmpython._backends.arm64.codegen import FuncCode, R_AARCH64_CALL26
from asmpython._backends.arm64.elf import build_elf


class Arm64LinuxLinkTests(unittest.TestCase):
    @staticmethod
    def _object_requiring(*symbols: str) -> bytes:
        relocs = [
            (index * 4, symbol, R_AARCH64_CALL26)
            for index, symbol in enumerate(symbols)
        ]
        return build_elf([FuncCode("main", bytes(4 * len(symbols)), relocs)])

    def test_cross_toolchain_discovery(self) -> None:
        resolved = {
            "aarch64-linux-gnu-as": "/tools/aarch64-linux-gnu-as",
            "aarch64-linux-gnu-ld": "/tools/aarch64-linux-gnu-ld",
        }
        with (
            patch.object(linux_link.platform, "machine", return_value="x86_64"),
            patch.object(linux_link.shutil, "which", side_effect=resolved.get),
        ):
            toolchain = linux_link.discover_toolchain("auto")

        self.assertFalse(toolchain.native)
        self.assertEqual(toolchain.assembler, resolved["aarch64-linux-gnu-as"])
        self.assertEqual(toolchain.linker, resolved["aarch64-linux-gnu-ld"])

    def test_native_toolchain_discovery(self) -> None:
        resolved = {"as": "/usr/bin/as", "ld": "/usr/bin/ld"}
        with (
            patch.object(linux_link.platform, "machine", return_value="aarch64"),
            patch.object(linux_link.shutil, "which", side_effect=resolved.get),
        ):
            toolchain = linux_link.discover_toolchain("auto")

        self.assertTrue(toolchain.native)
        self.assertEqual(toolchain.assembler, "/usr/bin/as")
        self.assertEqual(toolchain.linker, "/usr/bin/ld")

    def test_native_mode_rejects_non_arm_host(self) -> None:
        with patch.object(linux_link.platform, "machine", return_value="x86_64"):
            with self.assertRaisesRegex(
                linux_link.Arm64ToolchainError,
                "non-AArch64 host",
            ):
                linux_link.discover_toolchain("native")

    def test_missing_tool_reports_override_variable(self) -> None:
        with (
            patch.object(linux_link.platform, "machine", return_value="x86_64"),
            patch.object(linux_link.shutil, "which", return_value=None),
        ):
            with self.assertRaisesRegex(
                linux_link.Arm64ToolchainError,
                "ASMPYTHON_ARM64_AS",
            ):
                linux_link.discover_toolchain("cross")

    def test_start_source_validates_symbol(self) -> None:
        source = linux_link.start_source("user_main")
        self.assertIn("bl user_main", source)
        self.assertIn("mov x8, #93", source)
        with self.assertRaisesRegex(ValueError, "invalid AArch64 entry symbol"):
            linux_link.start_source("main; injected")

    def test_current_runtime_exports_satisfy_print_object(self) -> None:
        program = self._object_requiring("_abi_int_to_base", "printf")
        self.assertEqual(
            linux_link.required_external_symbols(program),
            frozenset({"_abi_int_to_base", "printf"}),
        )
        self.assertEqual(
            linux_link.validate_runtime_requirements(
                program,
                include_runtime=True,
            ),
            frozenset({"_abi_int_to_base", "printf"}),
        )

    def test_runtime_free_build_rejects_external_symbols(self) -> None:
        program = self._object_requiring("printf")
        with self.assertRaisesRegex(
            linux_link.Arm64LinkError,
            "runtime-free ARM64 build.*printf",
        ):
            linux_link.validate_runtime_requirements(
                program,
                include_runtime=False,
            )

    def test_unsupported_runtime_symbol_fails_before_tool_invocation(self) -> None:
        program = self._object_requiring("_abi_new_list")
        toolchain = linux_link.LinuxArm64Toolchain("as", "ld", False)
        with patch.object(linux_link, "build_start_object") as build_start:
            with self.assertRaisesRegex(
                linux_link.Arm64LinkError,
                "current freestanding ARM64 runtime.*_abi_new_list",
            ):
                linux_link.build_executable_from_object(
                    program,
                    toolchain=toolchain,
                    include_runtime=True,
                )
        build_start.assert_not_called()

    def test_invalid_object_is_reported_as_link_error(self) -> None:
        with self.assertRaisesRegex(
            linux_link.Arm64LinkError,
            "invalid ARM64 program object",
        ):
            linux_link.required_external_symbols(b"not an ELF object")

    def test_link_requires_at_least_one_object(self) -> None:
        toolchain = linux_link.LinuxArm64Toolchain("as", "ld", False)
        with self.assertRaisesRegex(ValueError, "at least one ARM64 object"):
            linux_link.link_objects([], toolchain=toolchain)

    def test_subprocess_error_preserves_diagnostic(self) -> None:
        failed = subprocess.CompletedProcess(
            ["ld"],
            1,
            stdout="",
            stderr="undefined reference to printf",
        )
        with patch.object(linux_link.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(
                linux_link.Arm64LinkError,
                "undefined reference to printf",
            ):
                linux_link._run(["ld"], stage="link")


if __name__ == "__main__":
    unittest.main()
