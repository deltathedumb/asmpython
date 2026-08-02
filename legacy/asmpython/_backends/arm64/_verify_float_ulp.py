"""Verify exact ARM64 math.ulp behavior."""
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


_ULP_SOURCE = """\
from math import inf, isinf, isnan, nan, ulp


def main() -> int:
    if ulp(0.0) <= 0.0:
        return 11
    if ulp(0.0) != ulp(1e-320):
        return 12
    if ulp(1.0) != 2.220446049250313e-16:
        return 13
    if ulp(2.0) != 4.440892098500626e-16:
        return 14
    if ulp(-2.0) != ulp(2.0):
        return 15
    if not isinf(ulp(inf)):
        return 16
    if not isnan(ulp(nan)):
        return 17
    if ulp(-0.0) != ulp(0.0):
        return 18
    print(1)
    return 0
"""
_EXPECTED_STDOUT = "1\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_math_isinf",
        "_math_isnan",
        "_math_ulp",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-ulp-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_ULP_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "ulp probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 ulp probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] zero and every subnormal used the minimum positive spacing")
        print("[ OK ] normal, negative, infinity, and NaN cases matched")
        print("[ OK ] exit-code checks avoided bool-formatting dependencies")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
