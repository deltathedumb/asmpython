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
from .module_codegen import compile_ir_module
from asmpython._compiler import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze


_SOURCE = """\
def main() -> int:
    return 40 + 2
"""


def _compile_source(source: str = _SOURCE) -> bytes:
    """Compile one source string through the real front end to ARM64 ET_REL."""
    tokens = Lexer(source).tokenize()
    module = Parser(tokens, frozenset()).parse()
    sema_analyze(module)
    ir_module = ir_lower.lower_module(module)
    return compile_ir_module(ir_module)


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
        if "Machine:" not in inspection or "AArch64" not in inspection or " main" not in inspection:
            print(inspection)
            raise SystemExit("generated source object is missing AArch64/main metadata")

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
                f"source-compiled AArch64 program returned {completed.returncode}, expected 42"
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
