from __future__ import annotations

from pathlib import Path


def replace_once(text: str, before: str, after: str, label: str) -> str:
    if before in text:
        return text.replace(before, after, 1)
    if after in text:
        return text
    raise RuntimeError(f"{label} pattern changed")


def update_cli_test() -> None:
    path = Path("tests/test_arm64_cli.py")
    text = path.read_text(encoding="utf-8")
    before = '''    def test_unsupported_runtime_fails_before_tool_discovery(self) -> None:
        unsupported = build_elf(
            [
                FuncCode(
                    "main",
                    bytes(4),
                    [(0, "_abi_new_list", R_AARCH64_CALL26)],
                )
            ]
        )
'''
    after = '''    def test_unsupported_runtime_fails_before_tool_discovery(self) -> None:
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
'''
    text = replace_once(text, before, after, "CLI unsupported symbol setup")
    text = replace_once(
        text,
        '            self.assertIn("_abi_new_list", errors.getvalue())\n',
        '            self.assertIn(unsupported_symbol, errors.getvalue())\n',
        "CLI unsupported symbol assertion",
    )
    path.write_text(text, encoding="utf-8")


def update_linker_tests() -> None:
    path = Path("tests/test_arm64_linux_link.py")
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from asmpython._backends.arm64.elf import build_elf\n",
        "from asmpython._backends.arm64.elf import build_elf\n"
        "from asmpython._backends.arm64.runtime_manifest import RUNTIME_SOURCE_NAMES\n",
        "manifest import",
    )
    old_sources = '''    def test_runtime_sources_are_modular_and_stable(self) -> None:
        paths = linux_link.runtime_source_paths()
        self.assertEqual(
            tuple(path.name for path in paths),
            (
                "abi_shims_linux_arm64.S",
                "abi_strings_linux_arm64.S",
                "abi_string_search_linux_arm64.S",
            ),
        )
        self.assertEqual(linux_link.runtime_source_path(), paths[0])
'''
    new_sources = '''    def test_runtime_sources_follow_the_manifest(self) -> None:
        paths = linux_link.runtime_source_paths()
        self.assertEqual(
            tuple(path.name for path in paths),
            RUNTIME_SOURCE_NAMES,
        )
        self.assertEqual(len(paths), len(set(paths)))
        self.assertGreaterEqual(len(paths), 3)
        self.assertEqual(linux_link.runtime_source_path(), paths[0])
'''
    text = replace_once(text, old_sources, new_sources, "runtime source test")
    text = replace_once(
        text,
        '        slices = [b"core-object", b"string-object", b"search-object"]\n',
        '        slices = [\n'
        '            f"runtime-slice-{index}".encode("ascii")\n'
        '            for index in range(len(RUNTIME_SOURCE_NAMES))\n'
        '        ]\n',
        "runtime slice fixtures",
    )
    old_assertions = '''        self.assertEqual(assemble.call_count, 3)
        self.assertEqual(
            [call.args[0].name for call in assemble.call_args_list],
            [
                "abi_shims_linux_arm64.S",
                "abi_strings_linux_arm64.S",
                "abi_string_search_linux_arm64.S",
            ],
        )
'''
    new_assertions = '''        self.assertEqual(assemble.call_count, len(RUNTIME_SOURCE_NAMES))
        self.assertEqual(
            [call.args[0].name for call in assemble.call_args_list],
            list(RUNTIME_SOURCE_NAMES),
        )
'''
    text = replace_once(text, old_assertions, new_assertions, "runtime slice assertions")
    old_unsupported = '''    def test_unsupported_runtime_symbol_fails_before_tool_invocation(self) -> None:
        program = self._object_requiring("_abi_new_list")
        toolchain = linux_link.LinuxArm64Toolchain("as", "ld", False)
        with patch.object(linux_link, "build_start_object") as build_start:
            with self.assertRaisesRegex(
                linux_link.Arm64LinkError,
                "current freestanding ARM64 runtime.*_abi_new_list",
            ):
'''
    new_unsupported = '''    def test_unsupported_runtime_symbol_fails_before_tool_invocation(self) -> None:
        unsupported_symbol = "_abi_list_slice"
        program = self._object_requiring(unsupported_symbol)
        toolchain = linux_link.LinuxArm64Toolchain("as", "ld", False)
        with patch.object(linux_link, "build_start_object") as build_start:
            with self.assertRaisesRegex(
                linux_link.Arm64LinkError,
                rf"current freestanding ARM64 runtime.*{unsupported_symbol}",
            ):
'''
    text = replace_once(text, old_unsupported, new_unsupported, "linker unsupported symbol")
    path.write_text(text, encoding="utf-8")


def update_replace_model() -> None:
    path = Path("tests/test_arm64_string_replace.py")
    text = path.read_text(encoding="utf-8")
    before = '''def _model_replace(text: str, old: str, new: str) -> str:
    if not old:
        return new + new.join(text) + new
'''
    after = '''def _model_replace(text: str, old: str, new: str) -> str:
    if not old:
        # Python inserts at every boundary. An empty string has one boundary,
        # not two; the generic non-empty formula would duplicate ``new``.
        if not text:
            return new
        return new + new.join(text) + new
'''
    path.write_text(
        replace_once(text, before, after, "empty replacement model"),
        encoding="utf-8",
    )


def main() -> None:
    update_cli_test()
    update_linker_tests()
    update_replace_model()


if __name__ == "__main__":
    main()
