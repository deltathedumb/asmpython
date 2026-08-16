"""Verify exact ARM64 math.isnan classification."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import build_executable_from_object, required_external_symbols, validate_runtime_requirements
from .source_build import compile_source_object


_ISNAN_SOURCE = """\
from math import isnan, nan


def main() -> int:
    print(isnan(nan), isnan(1.0), isnan(-nan))
    return 0
"""
_EXPECTED_STDOUT = "1 0 1\n"
_EXPECTED_REQUIREMENTS = frozenset({"_abi_int_to_base", "_math_isnan", "printf"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "native", "cross"), default="auto")
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-isnan-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_ISNAN_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(f"isnan probe external symbols: expected={sorted(_EXPECTED_REQUIREMENTS)}, actual={sorted(requirements)}")
        validate_runtime_requirements(program_blob, include_runtime=True)
        executable.write_bytes(build_executable_from_object(program_blob, toolchain=toolchain.build, entry_symbol="main", include_runtime=True))
        executable.chmod(0o755)
        completed = _execute(toolchain, executable)
        if completed.returncode != 0 or completed.stdout != _EXPECTED_STDOUT:
            print(f"expected stdout: {_EXPECTED_STDOUT!r}")
            print(f"actual stdout:   {completed.stdout!r}")
            print(completed.stderr, end="")
            raise SystemExit(f"freestanding ARM64 isnan probe failed: returncode={completed.returncode}")
        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] NaN payloads classified above infinity magnitude")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
