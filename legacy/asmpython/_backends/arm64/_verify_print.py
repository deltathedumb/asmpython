"""Verify the first freestanding AArch64 runtime slice with real source.

The probe intentionally performs a multi-argument print. IR lowering converts
all integer arguments before entering ``printf``; therefore this catches the
historical shared-static-buffer aliasing class as well as basic formatting,
linkage, Linux syscalls, and execution.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from ._verify_source import _compile_source
from .linux_link import build_executable_from_object


_PRINT_SOURCE = """\
def main() -> int:
    print(42)
    print(-10, 255, sep="|", end="!\\n")
    return 0
"""
_EXPECTED_STDOUT = "42\n-10|255!\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("auto", "native", "cross"),
        default="auto",
    )
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-print-") as tmp:
        root = Path(tmp)
        program_object = root / "program.o"
        executable = root / "program"

        program_blob = _compile_source(_PRINT_SOURCE)
        program_object.write_bytes(program_blob)

        inspection = subprocess.run(
            [toolchain.readelf, "--wide", "-r", "-s", str(program_object)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        required = ("_abi_int_to_base", "printf", "main")
        missing = [symbol for symbol in required if symbol not in inspection]
        if missing:
            print(inspection)
            raise SystemExit(f"print probe object is missing symbols: {missing}")

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
                "freestanding ARM64 print probe failed: "
                f"returncode={completed.returncode}"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] print(int) source lowered to _abi_int_to_base + printf")
        print("[ OK ] multi-argument conversions retained distinct buffers")
        print("[ OK ] reusable ARM64 builder linked the runtime and program")
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
