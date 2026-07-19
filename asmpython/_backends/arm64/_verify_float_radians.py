"""Verify ARM64 math.radians with the established binary64 factor."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import build_executable_from_object, required_external_symbols, validate_runtime_requirements
from .source_build import compile_source_object


_RADIANS_SOURCE = """\
from math import radians


def main() -> int:
    print(int(radians(0.0) * 1000000.0), int(radians(180.0) * 1000000.0), int(radians(-90.0) * 1000000.0))
    return 0
"""
_EXPECTED_STDOUT = "0 3141592 -1570796\n"
_EXPECTED_REQUIREMENTS = frozenset({"_abi_int_to_base", "_math_radians", "printf"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "native", "cross"), default="auto")
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-radians-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_RADIANS_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(f"radians probe external symbols: expected={sorted(_EXPECTED_REQUIREMENTS)}, actual={sorted(requirements)}")
        validate_runtime_requirements(program_blob, include_runtime=True)
        executable.write_bytes(build_executable_from_object(program_blob, toolchain=toolchain.build, entry_symbol="main", include_runtime=True))
        executable.chmod(0o755)
        completed = _execute(toolchain, executable)
        if completed.returncode != 0 or completed.stdout != _EXPECTED_STDOUT:
            print(f"expected stdout: {_EXPECTED_STDOUT!r}")
            print(f"actual stdout:   {completed.stdout!r}")
            print(completed.stderr, end="")
            raise SystemExit(f"freestanding ARM64 radians probe failed: returncode={completed.returncode}")
        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] radians used the established pi/180 binary64 factor")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
