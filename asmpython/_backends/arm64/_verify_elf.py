"""Build, link, inspect, and execute a minimal asmpython AArch64 object.

This verifies the complete Stage-1 path currently available:

``IRFunc -> regalloc -> codegen -> ELF ET_REL -> GNU ld -> qemu-aarch64``

The generated program exercises every relocation kind emitted by codegen:
``load_answer`` uses ADRP+ADD to address a global containing 42, ``caller``
uses BL to call it, and a tiny hand-written ``_start`` exits with the returned
value. Success is therefore a real process exit status of 42, not merely a
file-format parse.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from . import codegen, regalloc
from .elf import build_elf
from asmpython._compiler.ir import I64, PTR, IRBlock, IRFunc, IRGlobal, IRInstr, IRValue


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"missing {name}; install the AArch64 binutils/qemu toolchain")
    return path


def _compile_function(func: IRFunc) -> codegen.FuncCode:
    allocation = regalloc.allocate(func)
    return codegen.compile_func(func, allocation)


def _build_probe_object() -> bytes:
    address = IRValue("answer_address", PTR)
    value = IRValue("answer_value", I64)
    load_answer = IRFunc(
        "load_answer",
        [],
        I64,
        [
            IRBlock(
                "entry",
                [
                    IRInstr("global_addr", address, ["answer"]),
                    IRInstr("load", value, [address]),
                    IRInstr("ret", None, [value]),
                ],
            )
        ],
    )

    result = IRValue("call_result", I64)
    caller = IRFunc(
        "caller",
        [],
        I64,
        [
            IRBlock(
                "entry",
                [
                    IRInstr("call", result, ["load_answer"]),
                    IRInstr("ret", None, [result]),
                ],
            )
        ],
    )

    functions = [_compile_function(load_answer), _compile_function(caller)]
    return build_elf(functions, [IRGlobal("answer", I64, 42)])


def main() -> int:
    assembler = _require_tool("aarch64-linux-gnu-as")
    linker = _require_tool("aarch64-linux-gnu-ld")
    readelf = _require_tool("aarch64-linux-gnu-readelf")
    qemu = _require_tool("qemu-aarch64")

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-elf-") as tmp:
        root = Path(tmp)
        module_object = root / "module.o"
        start_source = root / "start.s"
        start_object = root / "start.o"
        executable = root / "probe"

        module_object.write_bytes(_build_probe_object())
        start_source.write_text(
            ".text\n"
            ".global _start\n"
            "_start:\n"
            "    bl caller\n"
            "    mov x8, #93\n"
            "    svc #0\n",
            encoding="utf-8",
        )

        subprocess.run(
            [assembler, "-o", str(start_object), str(start_source)],
            check=True,
        )

        inspection = subprocess.run(
            [readelf, "--wide", "-h", "-S", "-r", "-s", str(module_object)],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        required_markers = (
            "Machine:                           AArch64",
            "R_AARCH64_CALL26",
            "R_AARCH64_ADR_PREL_PG_HI21",
            "R_AARCH64_ADD_ABS_LO12_NC",
            "caller",
            "load_answer",
            "answer",
        )
        missing = [marker for marker in required_markers if marker not in inspection]
        if missing:
            print(inspection)
            raise SystemExit(f"readelf output missing expected markers: {missing}")

        subprocess.run(
            [
                linker,
                "-e",
                "_start",
                "-o",
                str(executable),
                str(start_object),
                str(module_object),
            ],
            check=True,
        )

        completed = subprocess.run([qemu, str(executable)], check=False)
        if completed.returncode != 42:
            raise SystemExit(
                f"AArch64 probe returned {completed.returncode}, expected 42"
            )

        print(f"[ OK ] ELF object: {module_object.stat().st_size} bytes")
        print("[ OK ] readelf recognized all three AArch64 relocation types")
        print("[ OK ] GNU ld linked the generated object")
        print("[ OK ] qemu-aarch64 process exited with 42")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
