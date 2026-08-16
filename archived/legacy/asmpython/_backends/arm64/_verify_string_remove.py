"""Verify exact ARM64 removeprefix/removesuffix runtime behavior."""
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


_REMOVE_SOURCE = """\
def main() -> int:
    print("unhappy".removeprefix("un"))
    print("unhappy".removeprefix("x"))
    print("archive.tar".removesuffix(".tar"))
    print("archive.tar".removesuffix(".zip"))
    print("éclair".removeprefix("é"))
    print("café".removesuffix("é"))
    print("same".removeprefix(""), "same".removesuffix(""), sep="|")
    return 0
"""
_EXPECTED_STDOUT = (
    "happy\n"
    "unhappy\n"
    "archive\n"
    "archive.tar\n"
    "clair\n"
    "caf\n"
    "same|same\n"
)
_EXPECTED_REQUIREMENTS = frozenset(
    {"_abi_str_removeprefix", "_abi_str_removesuffix", "printf"}
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-remove-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_REMOVE_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "string-remove probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 string-remove probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] matching and non-matching affixes matched Python values")
        print("[ OK ] empty affixes preserved the original string value")
        print("[ OK ] UTF-8 affixes were removed on complete code-point boundaries")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
