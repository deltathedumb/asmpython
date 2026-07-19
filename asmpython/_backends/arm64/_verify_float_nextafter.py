"""Verify exact ARM64 math.nextafter behavior."""
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


_NEXTAFTER_SOURCE = """\
from math import copysign, inf, isinf, isnan, nan, nextafter


def main() -> int:
    print(nextafter(1.0, 2.0) > 1.0, nextafter(1.0, 0.0) < 1.0)
    print(nextafter(0.0, 1.0) > 0.0, nextafter(0.0, -1.0) < 0.0)
    print(isinf(nextafter(inf, inf)), isinf(nextafter(inf, 0.0)))
    print(int(copysign(1.0, nextafter(0.0, -0.0))), isnan(nextafter(nan, 1.0)))
    return 0
"""
_EXPECTED_STDOUT = "1 1\n1 1\n1 0\n-1 1\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_math_isinf",
        "_math_isnan",
        "copysign",
        "nextafter",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-nextafter-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_NEXTAFTER_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "nextafter probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 nextafter probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] finite values moved exactly one representable bit step")
        print("[ OK ] zero, infinity, equality, and signed-zero targets matched")
        print("[ OK ] NaN operands remained NaN")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
