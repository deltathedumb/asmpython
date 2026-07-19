from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from asmpython._backends.arm64 import linux_link


class Arm64LinuxLinkTests(unittest.TestCase):
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
