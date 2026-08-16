"""Verify exact ARM64 C-fdim behavior."""
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


_FDIM_SOURCE = """\
from math import copysign, fdim, inf, isinf, isnan, nan


def main() -> int:
    print(int(fdim(7.5, 2.0) * 10.0), int(fdim(2.0, 7.5)))
    print(isinf(fdim(inf, 1.0)), int(copysign(1.0, fdim(-1.0, 2.0))))
    print(isnan(fdim(nan, 1.0)), isnan(fdim(1.0, nan)))
    return 0
"""
_EXPECTED_STDOUT = "55 0\n1 1\n1 1\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_math_isinf",
        "_math_isnan",
        "copysign",
        "fdim",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-fdim-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_FDIM_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "fdim probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 fdim probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] positive differences used one binary64 subtraction")
        print("[ OK ] non-positive differences returned canonical positive zero")
        print("[ OK ] NaN operands remained NaN")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
