"""Cross-checks encoder.py's output against a real `aarch64-linux-gnu-as`
assembly for every instruction form the encoder exposes. Not a pytest
file (this repo's test runner is tests/runner.py, source-level asmpython
programs) -- a standalone script meant to be run once against a real
cross-toolchain (e.g. inside WSL2, see roadmap.md's ARM64 Stage 0 notes)
to validate the hand-written bit patterns in encoder.py, since a wrong
encoding is not something ordinary code review reliably catches.

Usage (inside WSL2, with gcc-aarch64-linux-gnu/binutils-aarch64-linux-gnu
installed): `python3 _verify_encoder.py`
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from asmpython._backends.arm64 import encoder as E  # noqa: E402

AS = "aarch64-linux-gnu-as"
OBJDUMP = "aarch64-linux-gnu-objdump"


def _assemble(asm_text: str) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        s_path = Path(td) / "t.s"
        o_path = Path(td) / "t.o"
        s_path.write_text(".text\n" + asm_text + "\n", encoding="utf-8")
        proc = subprocess.run([AS, "-o", str(o_path), str(s_path)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"as failed for {asm_text!r}: {proc.stderr}")
        dump = subprocess.run(
            [OBJDUMP, "-d", "--insn-width=4", str(o_path)],
            check=True, capture_output=True, text=True,
        ).stdout
        words: list[bytes] = []
        for line in dump.splitlines():
            line = line.strip()
            if ":\t" not in line:
                continue
            hexpart = line.split(":\t", 1)[1].split("\t")[0].strip()
            hexpart = hexpart.replace(" ", "")
            if len(hexpart) != 8:
                continue
            words.append(bytes.fromhex(hexpart)[::-1])
        return b"".join(words)


CASES: list[tuple[str, bytes, str]] = [
    ("add x0, x1, x2", E.add_reg(E.Reg.X0, E.Reg.X1, E.Reg.X2), "add_reg"),
    ("sub x3, x4, x5", E.sub_reg(E.Reg.X3, E.Reg.X4, E.Reg.X5), "sub_reg"),
    ("subs xzr, x6, x7", E.cmp_reg(E.Reg.X6, E.Reg.X7), "cmp_reg"),
    ("and x8, x9, x10", E.and_reg(E.Reg.X8, E.Reg.X9, E.Reg.X10), "and_reg"),
    ("orr x11, x12, x13", E.orr_reg(E.Reg.X11, E.Reg.X12, E.Reg.X13), "orr_reg"),
    ("eor x14, x15, x16", E.eor_reg(E.Reg.X14, E.Reg.X15, E.Reg.X16), "eor_reg"),
    ("mul x17, x18, x19", E.mul(E.Reg.X17, E.Reg.X18, E.Reg.X19), "mul"),
    ("sdiv x20, x21, x22", E.sdiv(E.Reg.X20, E.Reg.X21, E.Reg.X22), "sdiv"),
    ("udiv x23, x24, x25", E.udiv(E.Reg.X23, E.Reg.X24, E.Reg.X25), "udiv"),
    ("mov x26, x27", E.mov_reg(E.Reg.X26, E.Reg.X27), "mov_reg"),
    ("neg x28, x0", E.neg_reg(E.Reg.X28, E.Reg.X0), "neg_reg"),
    ("add x0, x1, #100", E.add_imm(E.Reg.X0, E.Reg.X1, 100), "add_imm"),
    ("sub x2, x3, #200", E.sub_imm(E.Reg.X2, E.Reg.X3, 200), "sub_imm"),
    ("cmp x4, #42", E.cmp_imm(E.Reg.X4, 42), "cmp_imm"),
    ("movz x0, #0x1234", E.movz(E.Reg.X0, 0x1234), "movz"),
    ("movz x0, #0x1234, lsl #16", E.movz(E.Reg.X0, 0x1234, shift=16), "movz shift16"),
    ("movk x0, #0xabcd, lsl #32", E.movk(E.Reg.X0, 0xABCD, shift=32), "movk"),
    ("ldr x0, [x1, #16]", E.ldr_imm(E.Reg.X0, E.Reg.X1, 16), "ldr_imm"),
    ("str x2, [x3, #24]", E.str_imm(E.Reg.X2, E.Reg.X3, 24), "str_imm"),
    ("ldr w0, [x1, #8]", E.ldr_imm_w(E.Reg.X0, E.Reg.X1, 8), "ldr_imm_w"),
    ("str w2, [x3, #12]", E.str_imm_w(E.Reg.X2, E.Reg.X3, 12), "str_imm_w"),
    ("ldr d0, [x1, #32]", E.ldr_imm_d(E.VReg.V0, E.Reg.X1, 32), "ldr_imm_d"),
    ("str d2, [x3, #40]", E.str_imm_d(E.VReg.V2, E.Reg.X3, 40), "str_imm_d"),
    ("ldp x29, x30, [sp, #16]", E.ldp(E.Reg.X29, E.Reg.X30, E.Reg.SP, 16), "ldp offset"),
    ("ldp x0, x1, [sp], #32", E.ldp(E.Reg.X0, E.Reg.X1, E.Reg.SP, 32, writeback="post"), "ldp post"),
    ("stp x29, x30, [sp, #-32]!", E.stp(E.Reg.X29, E.Reg.X30, E.Reg.SP, -32, writeback="pre"), "stp pre"),
    ("stp x2, x3, [sp, #16]", E.stp(E.Reg.X2, E.Reg.X3, E.Reg.SP, 16), "stp offset"),
    ("b #0", E.b(0), "b zero"),
    ("b #16", E.b(4), "b +4 words"),
    ("bl #0", E.bl(0), "bl zero"),
    ("blr x5", E.blr(E.Reg.X5), "blr"),
    ("br x6", E.br(E.Reg.X6), "br"),
    ("ret", E.ret(), "ret default"),
    ("ret x9", E.ret(E.Reg.X9), "ret x9"),
    ("b.eq #0", E.b_cond(E.Cond.EQ, 0), "b.eq"),
    ("b.ne #0", E.b_cond(E.Cond.NE, 0), "b.ne"),
    ("b.lt #0", E.b_cond(E.Cond.LT, 0), "b.lt"),
    ("cbz x0, #0", E.cbz(E.Reg.X0, 0), "cbz"),
    ("cbnz x1, #0", E.cbnz(E.Reg.X1, 0), "cbnz"),
    ("adrp x0, .", E.adrp(E.Reg.X0, 0), "adrp zero"),
    ("adr x0, .", E.adr(E.Reg.X0, 0), "adr zero"),
    ("csel x0, x1, x2, eq", E.csel(E.Reg.X0, E.Reg.X1, E.Reg.X2, E.Cond.EQ), "csel"),
    ("csel x30, x29, x0, ge", E.csel(E.Reg.X30, E.Reg.X29, E.Reg.X0, E.Cond.GE), "csel x30/x29"),
    ("add x30, x29, #4095", E.add_imm(E.Reg.X30, E.Reg.X29, 4095), "add_imm max"),
    ("movz x9, #0xffff, lsl #48", E.movz(E.Reg.X9, 0xFFFF, shift=48), "movz shift48"),
    ("ldr x30, [x29, #4088]", E.ldr_imm(E.Reg.X30, E.Reg.X29, 4088), "ldr_imm max offset"),
    ("stp x29, x30, [sp, #504]", E.stp(E.Reg.X29, E.Reg.X30, E.Reg.SP, 504), "stp max positive"),
    ("ldp x0, x1, [sp, #-512]", E.ldp(E.Reg.X0, E.Reg.X1, E.Reg.SP, -512), "ldp min negative"),
    ("cset x0, eq", E.cset(E.Reg.X0, E.Cond.EQ), "cset x0 eq"),
    ("cset x5, ne", E.cset(E.Reg.X5, E.Cond.NE), "cset x5 ne"),
    ("cset x30, lt", E.cset(E.Reg.X30, E.Cond.LT), "cset x30 lt"),
    ("cset x12, gt", E.cset(E.Reg.X12, E.Cond.GT), "cset x12 gt"),
    ("nop", E.nop(), "nop"),
    ("svc #0", E.svc(0), "svc"),
    ("brk #0", E.brk(0), "brk"),
    ("fadd d0, d1, d2", E.fadd(E.VReg.V0, E.VReg.V1, E.VReg.V2), "fadd"),
    ("fsub d3, d4, d5", E.fsub(E.VReg.V3, E.VReg.V4, E.VReg.V5), "fsub"),
    ("fmul d6, d7, d8", E.fmul(E.VReg.V6, E.VReg.V7, E.VReg.V8), "fmul"),
    ("fdiv d9, d10, d11", E.fdiv(E.VReg.V9, E.VReg.V10, E.VReg.V11), "fdiv"),
    ("fneg d12, d13", E.fneg(E.VReg.V12, E.VReg.V13), "fneg"),
    ("fabs d14, d15", E.fabs_(E.VReg.V14, E.VReg.V15), "fabs"),
    ("fsqrt d16, d17", E.fsqrt(E.VReg.V16, E.VReg.V17), "fsqrt"),
    ("fmov d18, d19", E.fmov_reg(E.VReg.V18, E.VReg.V19), "fmov reg"),
    ("fmov d0, x1", E.fmov_from_gp(E.VReg.V0, E.Reg.X1), "fmov from gp"),
    ("fmov x2, d3", E.fmov_to_gp(E.Reg.X2, E.VReg.V3), "fmov to gp"),
    ("fcmp d4, d5", E.fcmp(E.VReg.V4, E.VReg.V5), "fcmp"),
    ("scvtf d0, x1", E.scvtf(E.VReg.V0, E.Reg.X1), "scvtf"),
    ("fcvtzs x0, d1", E.fcvtzs(E.Reg.X0, E.VReg.V1), "fcvtzs"),
    ("add x0, x1, #1, lsl #12", E.add_imm_lsl12(E.Reg.X0, E.Reg.X1, 1), "add lsl12"),
    ("sub x2, x3, #4095, lsl #12", E.sub_imm_lsl12(E.Reg.X2, E.Reg.X3, 4095), "sub lsl12 max"),
]


def main() -> int:
    failures = 0
    for asm_text, ours, label in CASES:
        real = _assemble(asm_text)
        status = "OK" if real == ours else "MISMATCH"
        if real != ours:
            failures += 1
        print(f"[{status:8}] {label:20} `{asm_text}`  real={real.hex()} ours={ours.hex()}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} encodings match real as(1) output")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
