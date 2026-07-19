"""Compile a real asmpython source program to AArch64 and execute it.

This is deliberately an integer-only runtime-free checkpoint. It exercises the
normal front end and IR lowering rather than constructing IR by hand:

``source -> lexer -> parser -> sema -> ir_lower -> ARM64 object -> builder -> run``

The default source defines ``main()`` returning ``40 + 2``. The shared Linux
builder supplies ``_start`` and links a runtime-free executable.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .linux_link import build_executable_from_object
from .source_build import compile_source_object


_SOURCE = """\
def main() -> int:
    return 40 + 2
"""


def _compile_source(source: str = _SOURCE) -> bytes:
    """Compatibility wrapper used by focused source/runtime tests."""
    return compile_source_object(source)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("auto", "native", "cross"),
        default="auto",
    )
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-source-") as tmp:
        root = Path(tmp)
        program_object = root / "program.o"
        executable = root / "program"

        program_blob = _compile_source()
        program_object.write_bytes(program_blob)

        inspection = subprocess.run(
            [toolchain.readelf, "--wide", "-h", "-r", "-s", str(program_object)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        if (
            "Machine:" not in inspection
            or "AArch64" not in inspection
            or " main" not in inspection
        ):
            print(inspection)
            raise SystemExit(
                "generated source object is missing AArch64/main metadata"
            )

        executable.write_bytes(
            build_executable_from_object(
                program_blob,
                toolchain=toolchain.build,
                entry_symbol="main",
                include_runtime=False,
            )
        )
        executable.chmod(0o755)

        completed = _execute(toolchain, executable)
        if completed.returncode != 42:
            print(completed.stdout, end="")
            print(completed.stderr, end="")
            raise SystemExit(
                "source-compiled AArch64 program returned "
                f"{completed.returncode}, expected 42"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] source parsed, type-checked, and lowered to IR")
        print("[ OK ] reusable ARM64 builder emitted a linkable executable")
        print(f"[ OK ] {mode_name} source program exited with 42")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
