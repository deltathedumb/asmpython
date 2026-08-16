"""Verify UTF-8 code-point semantics for ARM64 find/rfind runtime helpers."""
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


_FIND_SOURCE = """\
def main() -> int:
    text = "ébananana"
    print(text.find("ban"), text.find("ana"), text.rfind("ana"))
    print(text.find("", 0), text.rfind(""))
    pair = "éé"
    print(pair.find("é", 1), pair.find("é", -1), pair.find("é", 2))
    print(pair.find("", 2), pair.find("", 3))
    print("bananana".find("ana"), "bananana".rfind("ana"))
    return 0
"""
_EXPECTED_STDOUT = "1 2 6\n0 9\n1 1 -1\n2 -1\n1 5\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_abi_str_index_of",
        "_abi_str_index_of_start",
        "_abi_str_rindex_of",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-find-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_FIND_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "string-find probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 string-find probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] find/rfind returned Unicode code-point indices")
        print("[ OK ] overlapping, empty, negative-start, and past-end cases matched")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
