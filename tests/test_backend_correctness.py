from __future__ import annotations

import unittest

from tests.backend_correctness import (
    _normalize_output,
    _parse_block,
    _parse_extensions,
    _parse_stdin,
)


class BackendCorrectnessMetadataTests(unittest.TestCase):
    def test_expect_preserves_indentation_after_one_comment_space(self) -> None:
        src = "# expect:\n#   padded\n# plain\n\nprint('x')\n"
        self.assertEqual(_parse_block(src, "expect"), "  padded\nplain")

    def test_metadata_blocks_do_not_consume_each_other(self) -> None:
        src = (
            "# stdin:\n# Alice\n# 42\n"
            "# expect:\n# hello\n\nprint('hello')\n"
        )
        self.assertEqual(_parse_stdin(src), "Alice\n42\n")
        self.assertEqual(_parse_block(src, "expect"), "hello")

    def test_extension_marker_is_trimmed_and_ordered(self) -> None:
        src = "# ext: constants, custom.syntax , third\n# expect:\n# ok\n"
        self.assertEqual(_parse_extensions(src), ["constants", "custom.syntax", "third"])

    def test_output_normalization_matches_runner_policy(self) -> None:
        self.assertEqual(_normalize_output("a  \r\nb\r\n"), "a\nb")


if __name__ == "__main__":
    unittest.main()
