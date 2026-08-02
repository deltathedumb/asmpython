"""Verify the exact scalar/string freestanding AArch64 runtime slice.

This source intentionally exercises every symbol added by
``abi_strings_linux_arm64.S`` plus bool/None lowering, which requires no runtime
conversion helper.  Exact stdout catches ABI, allocation, comparison, prefix,
and multi-value formatting errors.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import (
    build_executable_from_object,
    required_external_symbols,
    validate_runtime_requirements,
)
from .source_build import compile_source_object


_SCALAR_SOURCE = """\
def main() -> int:
    left = "ab"
    right = "cd"
    print(True, None, hex(-10), oct(8), bin(5), abs(-42))
    print(left + right)
    print(left == "ab", left != right, left < right, right > left)
    return 0
"""
_EXPECTED_STDOUT = (
    "True None -0xa 0o10 0b101 42\n"
    "abcd\n"
    "True True True True\n"
)
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_abi_str_cmp",
        "_abi_str_concat",
        "_abi_str_eq",
        "labs",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-scalars-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_SCALAR_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "scalar probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 scalar/string probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] bool and None formatting stayed entirely in lowered IR")
        print("[ OK ] base-prefixed integers and labs matched Python output")
        print("[ OK ] string concat/equality/order matched Python output")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
