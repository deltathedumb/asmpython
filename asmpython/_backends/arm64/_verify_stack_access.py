"""Verify ARM64 stack-access wrapper bytes against GNU binutils.

Run inside the WSL2 AArch64 toolchain environment described in RESUME.md::

    python -m asmpython._backends.arm64._verify_stack_access

The script assembles each equivalent instruction sequence with real
``aarch64-linux-gnu-as`` and compares the raw ``.text`` bytes to what the
backend emits. It intentionally covers both single-instruction LDUR/STUR
and the multi-instruction fallbacks used for large stack frames/offsets.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from . import encoder


Case = tuple[str, str, Callable[[], bytes]]

_CASES: list[Case] = [
    (
        "negative GP load",
        "ldur x9, [x29, #-8]",
        lambda: encoder.ldr_imm(encoder.Reg.X9, encoder.Reg.X29, -8),
    ),
    (
        "negative GP store",
        "stur x10, [x29, #-16]",
        lambda: encoder.str_imm(encoder.Reg.X10, encoder.Reg.X29, -16),
    ),
    (
        "negative FP load",
        "ldur d14, [x29, #-24]",
        lambda: encoder.ldr_imm_d(encoder.VReg.V14, encoder.Reg.X29, -24),
    ),
    (
        "negative FP store",
        "stur d15, [x29, #-32]",
        lambda: encoder.str_imm_d(encoder.VReg.V15, encoder.Reg.X29, -32),
    ),
    (
        "large negative GP load",
        "sub x16, x29, #2, lsl #12\nldr x9, [x16]",
        lambda: encoder.ldr_imm(encoder.Reg.X9, encoder.Reg.X29, -8192),
    ),
    (
        "large frame push",
        "sub sp, sp, #1, lsl #12\nsub sp, sp, #512\nstp x29, x30, [sp]",
        lambda: encoder.stp(
            encoder.Reg.X29,
            encoder.Reg.X30,
            encoder.Reg.SP,
            -4608,
            writeback="pre",
        ),
    ),
    (
        "large frame pop",
        "ldp x29, x30, [sp]\nadd sp, sp, #1, lsl #12\nadd sp, sp, #512",
        lambda: encoder.ldp(
            encoder.Reg.X29,
            encoder.Reg.X30,
            encoder.Reg.SP,
            4608,
            writeback="post",
        ),
    ),
]


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise SystemExit(
            f"missing {name}; install binutils-aarch64-linux-gnu before running"
        )
    return path


def _assemble(instructions: str) -> bytes:
    assembler = _require_tool("aarch64-linux-gnu-as")
    objcopy = _require_tool("aarch64-linux-gnu-objcopy")

    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-stack-") as tmp:
        root = Path(tmp)
        source = root / "case.s"
        obj = root / "case.o"
        raw = root / "case.bin"
        body = "\n".join(f"    {line}" for line in instructions.splitlines())
        source.write_text(
            ".text\n.global _start\n_start:\n" + body + "\n",
            encoding="utf-8",
        )
        subprocess.run([assembler, "-o", str(obj), str(source)], check=True)
        subprocess.run(
            [objcopy, "-O", "binary", "-j", ".text", str(obj), str(raw)],
            check=True,
        )
        return raw.read_bytes()


def main() -> int:
    failures = 0
    for name, assembly, emit in _CASES:
        expected = _assemble(assembly)
        actual = emit()
        if actual != expected:
            failures += 1
            print(f"[FAIL] {name}")
            print(f"  assembly: {assembly!r}")
            print(f"  expected: {expected.hex()}")
            print(f"  actual:   {actual.hex()}")
        else:
            print(f"[ OK ] {name}: {actual.hex()}")

    print(f"\n{len(_CASES) - failures}/{len(_CASES)} stack-access cases verified")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
