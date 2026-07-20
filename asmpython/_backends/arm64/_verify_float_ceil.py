"""Verify ARM64 math.ceil integer-return behavior."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import build_executable_from_object, required_external_symbols, validate_runtime_requirements
from .source_build import compile_source_object

_CEIL_SOURCE = """\
from math import ceil

def main() -> int:
    if ceil(2.1) != 3:
        return 11
    if ceil(-2.9) != -2:
        return 12
    if ceil(-0.2) != 0:
        return 13
    print(1)
    return 0
"""
_EXPECTED_STDOUT = "1\n"
_EXPECTED_REQUIREMENTS = frozenset({"_abi_int_to_base", "ceil", "printf"})

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("auto", "native", "cross"), default="auto")
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-ceil-") as tmp:
        executable = Path(tmp) / "program"
        blob = compile_source_object(_CEIL_SOURCE)
        actual = required_external_symbols(blob)
        if actual != _EXPECTED_REQUIREMENTS:
            raise SystemExit(f"ceil probe external symbols: expected={sorted(_EXPECTED_REQUIREMENTS)}, actual={sorted(actual)}")
        validate_runtime_requirements(blob, include_runtime=True)
        executable.write_bytes(build_executable_from_object(blob, toolchain=toolchain.build, entry_symbol="main", include_runtime=True))
        executable.chmod(0o755)
        completed = _execute(toolchain, executable)
        if completed.returncode != 0 or completed.stdout != _EXPECTED_STDOUT:
            raise SystemExit(f"ARM64 ceil probe failed: returncode={completed.returncode}, stdout={completed.stdout!r}")
        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] ceil returned Python integers for positive and negative inputs")
        print("[ OK ] finite near-zero input converted to integer zero")
        print(f"[ OK ] {mode_name} ceil matched {_EXPECTED_STDOUT!r}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
