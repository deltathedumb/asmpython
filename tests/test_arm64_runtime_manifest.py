from __future__ import annotations

import unittest

from asmpython._backends.arm64 import linux_link
from asmpython._backends.arm64.runtime_manifest import (
    RUNTIME_EXPORTS,
    RUNTIME_SOURCE_NAMES,
)


class Arm64RuntimeManifestTests(unittest.TestCase):
    def test_linker_uses_manifest_objects_directly(self) -> None:
        self.assertIs(linux_link.RUNTIME_EXPORTS, RUNTIME_EXPORTS)
        self.assertIs(linux_link.RUNTIME_SOURCE_NAMES, RUNTIME_SOURCE_NAMES)

    def test_repeat_symbol_is_advertised_by_the_single_manifest(self) -> None:
        self.assertIn("_abi_str_repeat", RUNTIME_EXPORTS)
        self.assertEqual(
            RUNTIME_SOURCE_NAMES,
            (
                "abi_shims_linux_arm64.S",
                "abi_strings_linux_arm64.S",
                "abi_string_search_linux_arm64.S",
            ),
        )

    def test_runtime_paths_follow_manifest_order(self) -> None:
        self.assertEqual(
            tuple(path.name for path in linux_link.runtime_source_paths()),
            RUNTIME_SOURCE_NAMES,
        )


if __name__ == "__main__":
    unittest.main()
