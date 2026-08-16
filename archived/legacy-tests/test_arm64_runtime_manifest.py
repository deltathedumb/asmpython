from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from asmpython._backends.arm64 import linux_link
from asmpython._backends.arm64.runtime_manifest import (
    RUNTIME_EXPORTS,
    RUNTIME_SLICES,
    RUNTIME_SOURCE_NAMES,
    RuntimeSlice,
    declared_global_symbols,
    validate_slice_source,
)


class Arm64RuntimeManifestTests(unittest.TestCase):
    def test_linker_uses_manifest_objects_directly(self) -> None:
        self.assertIs(linux_link.RUNTIME_EXPORTS, RUNTIME_EXPORTS)
        self.assertIs(linux_link.RUNTIME_SOURCE_NAMES, RUNTIME_SOURCE_NAMES)

    def test_flat_surface_is_derived_from_owned_slices(self) -> None:
        self.assertIn("_abi_str_repeat", RUNTIME_EXPORTS)
        self.assertEqual(
            RUNTIME_SOURCE_NAMES,
            tuple(runtime_slice.filename for runtime_slice in RUNTIME_SLICES),
        )
        self.assertEqual(
            RUNTIME_EXPORTS,
            frozenset(
                symbol
                for runtime_slice in RUNTIME_SLICES
                for symbol in runtime_slice.exports
            ),
        )

    def test_global_directive_parser_handles_alias_and_lists(self) -> None:
        self.assertEqual(
            declared_global_symbols(
                ".global one, two\n.globl three\n.global four five\n"
            ),
            frozenset({"one", "two", "three", "four", "five"}),
        )

    def test_slice_validator_rejects_missing_and_unexpected_exports(self) -> None:
        runtime_slice = RuntimeSlice("slice.S", frozenset({"one", "two"}))
        with self.assertRaisesRegex(ValueError, "missing two"):
            validate_slice_source(runtime_slice, ".global one\n")
        with self.assertRaisesRegex(ValueError, "unexpected three"):
            validate_slice_source(
                runtime_slice,
                ".global one\n.global two, three\n",
            )

    def test_runtime_paths_follow_manifest_order(self) -> None:
        self.assertEqual(
            tuple(path.name for path in linux_link.runtime_source_paths()),
            RUNTIME_SOURCE_NAMES,
        )

    def test_linker_rejects_source_order_drift_before_assembly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = tuple(root / name for name in reversed(RUNTIME_SOURCE_NAMES))
            for path in paths:
                path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(
                linux_link.Arm64LinkError,
                "source order does not match manifest",
            ):
                linux_link.validate_runtime_source_files(paths)


if __name__ == "__main__":
    unittest.main()
