"""Verify freestanding ARM64 positive-step list slicing."""
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


_SLICE_SOURCE = """\
def main() -> int:
    xs = [0, 1, 2, 3, 4]
    middle = xs[1:4]
    front = xs[:3]
    tail = xs[2:]
    negative = xs[-4:-1]
    empty = xs[4:2]
    clipped = xs[-99:99]

    a, b, c = middle
    if a != 1 or b != 2 or c != 3:
        return 11
    d, e, f = front
    if d != 0 or e != 1 or f != 2:
        return 12
    g, h, i = tail
    if g != 2 or h != 3 or i != 4:
        return 13
    j, k, l = negative
    if j != 1 or k != 2 or l != 3:
        return 14
    if len(empty) != 0:
        return 15
    m, n, o, p, q = clipped
    if m != 0 or n != 1 or o != 2 or p != 3 or q != 4:
        return 16
    print(len(middle), len(front), len(tail), len(negative), len(empty), len(clipped))
    return 0
"""
_EXPECTED_STDOUT = "3 3 3 3 0 5\n"
_EXPECTED_REQUIREMENTS = frozenset(
    {
        "_abi_int_to_base",
        "_abi_list_append",
        "_abi_list_slice",
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-list-slice-") as tmp:
        executable = Path(tmp) / "program"
        program_blob = compile_source_object(_SLICE_SOURCE)
        requirements = required_external_symbols(program_blob)
        if requirements != _EXPECTED_REQUIREMENTS:
            raise SystemExit(
                "list-slice probe lowered to an unexpected external-symbol set: "
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
                "freestanding ARM64 list-slice probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] explicit, missing, and negative bounds normalized correctly")
        print("[ OK ] clipped and reversed bounds produced Python-compatible lengths")
        print("[ OK ] every result was a newly allocated shallow-copy list")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
