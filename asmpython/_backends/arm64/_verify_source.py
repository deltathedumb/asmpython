"""Compile a real asmpython source program to AArch64 and execute it.

This is deliberately an integer-only runtime-free checkpoint. It exercises the
normal front end and IR lowering rather than constructing IR by hand:

``source -> lexer -> parser -> sema -> ir_lower -> ARM64 object -> ld -> run``

The source defines ``main()`` returning ``40 + 2``. A tiny freestanding
``_start`` calls that symbol and exits with its result, so success is a real
native/QEMU process exit status of 42.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

from ._verify_elf import _execute, _select_toolchain
from .module_codegen import compile_ir_module
from asmpython._compiler import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze


_SOURCE = """\
def main() -> int:
    return 40 + 2
"""


def _compile_source() -> bytes:
    tokens = Lexer(_SOURCE).tokenize()
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
        start_source = root / "start.s"
        start_object = root / "start.o"
        executable = root / "program"

        program_object.write_bytes(_compile_source())
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
            [toolchain.readelf, "--wide", "-h", "-r", "-s", str(program_object)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        if "Machine:" not in inspection or "AArch64" not in inspection or " main" not in inspection:
            print(inspection)
            raise SystemExit("generated source object is missing AArch64/main metadata")

        subprocess.run(
            [
                toolchain.linker,
                "-e",
                "_start",
                "-o",
                str(executable),
                str(start_object),
                str(program_object),
            ],
            check=True,
        )

        completed = _execute(toolchain, executable)
        if completed.returncode != 42:
            print(completed.stdout, end="")
            print(completed.stderr, end="")
            raise SystemExit(
                f"source-compiled AArch64 program returned {completed.returncode}, expected 42"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print("[ OK ] source parsed, type-checked, and lowered to IR")
        print("[ OK ] ARM64 module compiler emitted a linkable ELF object")
        print(f"[ OK ] {mode_name} source program exited with 42")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
