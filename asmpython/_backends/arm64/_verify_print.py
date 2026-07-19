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


_PRINT_SOURCE = """\
def main() -> int:
    print(42)
    print(-10, 255, sep="|", end="!\\n")
    return 0
"""
_EXPECTED_STDOUT = "42\n-10|255!\n"


def _runtime_source() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "_runtime"
        / "abi_shims_linux_arm64.S"
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

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-print-") as tmp:
        root = Path(tmp)
        program_object = root / "program.o"
        runtime_object = root / "runtime.o"
        start_source = root / "start.s"
        start_object = root / "start.o"
        executable = root / "program"

        program_object.write_bytes(_compile_source(_PRINT_SOURCE))
        subprocess.run(
            [toolchain.assembler, "-o", str(runtime_object), str(_runtime_source())],
            check=True,
        )
        start_source.write_text(
            ".text\n"
            ".global _start\n"
            "_start:\n"
            "    bl main\n"
            "    mov x8, #93\n"
            "    svc #0\n",
            encoding="utf-8",
        )
        subprocess.run(
            [toolchain.assembler, "-o", str(start_object), str(start_source)],
            check=True,
        )

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

        subprocess.run(
            [
                toolchain.linker,
                "-e",
                "_start",
                "-o",
                str(executable),
                str(start_object),
                str(program_object),
                str(runtime_object),
            ],
            check=True,
        )

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
        print(f"[ OK ] {mode_name} stdout matched {_EXPECTED_STDOUT!r}")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
