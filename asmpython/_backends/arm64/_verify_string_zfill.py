"""Verify exact Unicode-width ARM64 str.zfill behavior."""
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


_ZFILL_SOURCE = """\
def main() -> int:
    print("42".zfill(5))
    print("-42".zfill(5))
    print("+42".zfill(5))
    print("é".zfill(3))
    print("🙂".zfill(3))
    print("abc".zfill(2), "abc".zfill(-1), sep="|")
    print("".zfill(3))
    return 0
"""
_EXPECTED_STDOUT = "00042\n-0042\n+0042\n00é\n00🙂\nabc|abc\n000\n"
_EXPECTED_REQUIREMENTS = frozenset({"_abi_str_zfill", "printf"})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("auto", "native", "cross"),
        default="auto",
    )
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-zfill-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_ZFILL_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "string-zfill probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 string-zfill probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] sign characters remained before inserted zeroes")
        print("[ OK ] width was measured in Unicode code points")
        print("[ OK ] short, negative-width, and empty inputs matched Python")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
