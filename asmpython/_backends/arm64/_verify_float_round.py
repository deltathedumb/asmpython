"""Verify exact ARM64 C-round (ties away from zero) behavior."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import build_executable_from_object, required_external_symbols, validate_runtime_requirements
from .source_build import compile_source_object


_ROUND_SOURCE = """\
from math import copysign, inf, isinf, isnan, nan, round


def main() -> int:
    print(int(round(2.5)), int(round(-2.5)), int(round(2.49)))
    print(int(copysign(1.0, round(-0.2))), isinf(round(inf)), isnan(round(nan)))
    return 0
"""
_EXPECTED_STDOUT = "3 -3 2\n-1 1 1\n"
_EXPECTED_REQUIREMENTS = frozenset({"_abi_int_to_base", "_math_isinf", "_math_isnan", "copysign", "printf", "round"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "native", "cross"), default="auto")
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-round-") as tmp:
        executable = Path(tmp) / "program"
        blob = compile_source_object(_ROUND_SOURCE)
        actual = required_external_symbols(blob)
        if actual != _EXPECTED_REQUIREMENTS:
            raise SystemExit(f"round probe external symbols: expected={sorted(_EXPECTED_REQUIREMENTS)}, actual={sorted(actual)}")
        validate_runtime_requirements(blob, include_runtime=True)
        executable.write_bytes(build_executable_from_object(blob, toolchain=toolchain.build, entry_symbol="main", include_runtime=True))
        executable.chmod(0o755)
        completed = _execute(toolchain, executable)
        if completed.returncode != 0 or completed.stdout != _EXPECTED_STDOUT:
            raise SystemExit(f"ARM64 round probe failed: returncode={completed.returncode}, stdout={completed.stdout!r}")
        print(f"[ OK ] {'native AArch64' if toolchain.native else 'qemu-aarch64'} round matched {_EXPECTED_STDOUT!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
