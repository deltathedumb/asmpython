"""Verify exact ARM64 math.modf_frac behavior."""
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


_MODF_FRAC_SOURCE = """\
from math import copysign, inf, isnan, modf_frac, nan


def main() -> int:
    print(int(modf_frac(3.75) * 100.0), int(modf_frac(-3.75) * 100.0))
    print(int(copysign(1.0, modf_frac(-2.0))), int(copysign(1.0, modf_frac(inf))))
    print(isnan(modf_frac(nan)))
    return 0
"""
_EXPECTED_STDOUT = "75 -75\n-1 1\n1\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {"_abi_int_to_base", "_math_isnan", "_math_modf_frac", "copysign", "printf"}
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-modf-frac-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_MODF_FRAC_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "modf_frac probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 modf_frac probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] finite fractional parts matched binary64 truncation")
        print("[ OK ] integral and infinite inputs produced correctly signed zero")
        print("[ OK ] NaN remained classified as NaN")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
