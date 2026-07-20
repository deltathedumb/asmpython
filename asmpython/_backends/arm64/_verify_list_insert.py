"""Verify freestanding ARM64 list.insert() index clamping and shifting."""
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


_INSERT_SOURCE = """\
def main() -> int:
    xs = [1, 3]
    xs.insert(1, 2)
    xs.insert(-99, 0)
    xs.insert(99, 4)
    a, b, c, d, e = xs
    if a != 0:
        return 11
    if b != 1:
        return 12
    if c != 2:
        return 13
    if d != 3:
        return 14
    if e != 4:
        return 15
    print(len(xs))
    return 0
"""
_EXPECTED_STDOUT = "5\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_abi_list_append",
        "_abi_list_insert",
        "_abi_new_list",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-list-insert-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_INSERT_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "list-insert probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 list-insert probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] middle insertion shifted active cells right")
        print("[ OK ] very-negative insertion clamped to the front")
        print("[ OK ] oversized insertion clamped to the end")
        print("[ OK ] exit checks observed final cells [0, 1, 2, 3, 4]")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
