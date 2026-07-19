"""Verify exact ARM64 startswith/endswith/count runtime behavior."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import (
    build_executable_from_object,
    required_external_symbols,
    validate_runtime_requirements,
)
from .source_build import compile_source_object


_SEARCH_SOURCE = """\
def main() -> int:
    ascii_text = "bananana"
    unicode_text = "éé"
    print(
        ascii_text.startswith("ban"),
        ascii_text.endswith("ana"),
        ascii_text.count("ana"),
        ascii_text.count(""),
    )
    print(
        unicode_text.startswith("é"),
        unicode_text.endswith("é"),
        unicode_text.count("é"),
        unicode_text.count(""),
    )
    return 0
"""
_EXPECTED_STDOUT = "True True 2 9\nTrue True 2 3\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_abi_str_count",
        "_abi_str_ends_with",
        "_abi_str_starts_with",
        "printf",
    }
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("auto", "native", "cross"),
        default="auto",
    )
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-search-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_SEARCH_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "string-search probe lowered to an unexpected external-symbol set: "
                f"expected={sorted(_EXPECTED_REQUIREMENTS)}, "
                f"actual={sorted(requirements)}"
            )
        validate_runtime_requirements(program_blob, include_runtime=True)

        executable.write_bytes(
            build_executable_from_object(
                program_blob,
                toolchain=toolchain.build,
                entry_symbol="main",
                include_runtime=True,
            )
        )
        executable.chmod(0o755)

        completed = _execute(toolchain, executable)
        if completed.returncode != 0 or completed.stdout != _EXPECTED_STDOUT:
            print(f"expected stdout: {_EXPECTED_STDOUT!r}")
            print(f"actual stdout:   {completed.stdout!r}")
            print(completed.stderr, end="")
            raise SystemExit(
                "freestanding ARM64 string-search probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] startswith and endswith matched Python behavior")
        print("[ OK ] non-overlapping substring count matched Python behavior")
        print("[ OK ] empty substring count used UTF-8 code-point length")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
