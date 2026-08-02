"""Build, link, inspect, and execute a minimal asmpython AArch64 object.

This verifies the complete Stage-1 path currently available:

``IRFunc -> regalloc -> codegen -> ELF ET_REL -> reusable linker -> execution``

Execution can happen natively on an AArch64 host or through qemu-aarch64 on an
x86-64 host. The generated program exercises every relocation kind emitted by
codegen: ``load_answer`` uses ADRP+ADD to address a global containing 42,
``caller`` uses BL to call it, and the shared Linux builder supplies ``_start``.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import codegen, regalloc
from .elf import build_elf
from .linux_link import (
    Arm64ToolchainError,
    LinuxArm64Toolchain,
    build_executable_from_object,
    discover_toolchain,
)
from asmpython._compiler.ssa.ir import (
    I64,
    PTR,
    IRBlock,
    IRFunc,
    IRGlobal,
    IRInstr,
    IRValue,
)


@dataclass(frozen=True)
class Toolchain:
    build: LinuxArm64Toolchain
    readelf: str
    qemu: str | None = None
    strace: str | None = None

    @property
    def native(self) -> bool:
        return self.build.native


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"missing required verification tool: {name}")
    return path


def _select_toolchain(mode: str) -> Toolchain:
    try:
        build = discover_toolchain(mode)
    except Arm64ToolchainError as exc:
        raise SystemExit(str(exc)) from exc

    if build.native:
        return Toolchain(
            build=build,
            readelf=_require_tool("readelf"),
            strace=_require_tool("strace"),
        )
    return Toolchain(
        build=build,
        readelf=_require_tool("aarch64-linux-gnu-readelf"),
        qemu=_require_tool("qemu-aarch64"),
    )


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


def _verify_readelf(inspection: str) -> None:
    required_markers = (
        "AArch64",
        "R_AARCH64_CALL26",
        "R_AARCH64_ADR_PREL_PG_HI21",
        "R_AARCH64_ADD_ABS_LO12_NC",
        "caller",
        "load_answer",
        "answer",
    )
    missing = [marker for marker in required_markers if marker not in inspection]
    if "Machine:" not in inspection or missing:
        print(inspection)
        raise SystemExit(f"readelf output missing expected markers: {missing}")


def _execute(toolchain: Toolchain, executable: Path) -> subprocess.CompletedProcess[str]:
    if toolchain.native:
        assert toolchain.strace is not None
        command = [
            toolchain.strace,
            "-f",
            "-e",
            "trace=exit,exit_group",
            str(executable),
        ]
    else:
        assert toolchain.qemu is not None
        command = [toolchain.qemu, "-strace", str(executable)]
    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("auto", "native", "cross"),
        default="auto",
        help="execute natively or through qemu-aarch64 (default: auto)",
    )
    args = parser.parse_args(argv)
    toolchain = _select_toolchain(args.mode)

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-elf-") as tmp:
        root = Path(tmp)
        module_object = root / "module.o"
        executable = root / "probe"

        module_blob = _build_probe_object()
        module_object.write_bytes(module_blob)

        inspection = subprocess.run(
            [
                toolchain.readelf,
                "--wide",
                "-h",
                "-S",
                "-r",
                "-s",
                str(module_object),
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        _verify_readelf(inspection)

        executable.write_bytes(
            build_executable_from_object(
                module_blob,
                toolchain=toolchain.build,
                entry_symbol="caller",
                include_runtime=False,
            )
        )
        executable.chmod(0o755)

        completed = _execute(toolchain, executable)
        if completed.returncode != 42:
            print(completed.stdout, end="")
            print(completed.stderr, end="")
            raise SystemExit(
                f"AArch64 probe returned {completed.returncode}, expected 42"
            )

        mode_name = "native AArch64" if toolchain.native else "qemu-aarch64"
        print(f"[ OK ] ELF object: {module_object.stat().st_size} bytes")
        print("[ OK ] readelf recognized all three AArch64 relocation types")
        print("[ OK ] reusable Linux ARM64 builder linked the generated object")
        print(f"[ OK ] {mode_name} process exited with 42")
        if completed.stderr:
            print(completed.stderr.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
