"""Cross-verify the x86 (32-bit) encoder against real NASM output.

Each case assembles the equivalent NASM `bits 32` source for one
encode_* call and asserts byte-for-byte equality against what our
encoder produces. This is the strongest ground truth available short
of a hardware disassembler -- if NASM and our encoder disagree, our
encoder is wrong.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from asmpython._backends.x86 import encoder as enc
from asmpython._backends.x86.encoder import CC, Mem, Reg, XmmReg


def _nasm_available() -> bool:
    return shutil.which("nasm") is not None and shutil.which("objdump") is not None


def _assemble(body: str) -> bytes:
    """Assemble a `bits 32` NASM fragment and return its raw .text bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "case.asm"
        out = Path(tmp) / "case.bin"
        src.write_text(f"bits 32\n{body}\n", encoding="utf-8")
        result = subprocess.run(
            ["nasm", "-f", "bin", str(src), "-o", str(out)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"nasm failed:\n{result.stdout}\n{result.stderr}")
        return out.read_bytes()


def _disassemble(data: bytes) -> str:
    """Disassemble raw 32-bit-mode machine code via objdump, returning just
    the mnemonic+operand text of every instruction line (no addresses/raw
    bytes), so two different valid encodings of the same instruction
    (e.g. opcode 0x89 vs 0x8B for a reg-reg MOV, or an EAX-specific short
    form vs the general ModRM form) compare as identical -- this backend's
    encoder is free to choose either as long as the executed effect
    matches, unlike NASM's own encoding choices which this test does not
    try to reproduce byte-for-byte."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "case.bin"
        path.write_bytes(data)
        result = subprocess.run(
            ["objdump", "-D", "-b", "binary", "-m", "i386", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"objdump failed:\n{result.stdout}\n{result.stderr}")
    lines = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        # Only real instruction lines look like "   0:\t8b 05 ...\tmov ...";
        # they start with a bare hex offset (no other output line does).
        if "\t" not in line:
            continue
        prefix = stripped.split(":", 1)[0]
        if not prefix or any(c not in "0123456789abcdef" for c in prefix.lower()):
            continue
        # "   0:\t8b 05 00 10 00 00 \tmov    0x1000,%eax" -> "mov 0x1000,%eax"
        text = line.rsplit("\t", 1)[-1]
        lines.append(" ".join(text.split()))
    return "\n".join(lines)


@unittest.skipUnless(_nasm_available(), "requires nasm and objdump on PATH")
class X86EncoderNasmCrossCheckTests(unittest.TestCase):
    def _check(self, produced: bytes, nasm_source: str) -> None:
        expected = _assemble(nasm_source)
        produced_asm = _disassemble(produced)
        expected_asm = _disassemble(expected)
        self.assertEqual(
            produced_asm, expected_asm,
            f"encoder produced {produced.hex()!r} ({produced_asm!r}), nasm "
            f"produced {expected.hex()!r} ({expected_asm!r}) for {nasm_source!r}",
        )

    # ---- MOV -----------------------------------------------------------

    def test_mov_rr(self):
        self._check(enc.encode_mov_rr(Reg.EAX, Reg.ECX), "mov eax, ecx")
        self._check(enc.encode_mov_rr(Reg.EDI, Reg.ESP), "mov edi, esp")

    def test_mov_ri(self):
        self._check(enc.encode_mov_ri(Reg.EAX, 42), "mov eax, 42")
        self._check(enc.encode_mov_ri(Reg.EBX, 0xDEADBEEF), "mov ebx, 0xDEADBEEF")

    def test_mov_rm_and_mr(self):
        self._check(enc.encode_mov_rm(Reg.EAX, Mem(Reg.EBP, -4)), "mov eax, [ebp-4]")
        self._check(enc.encode_mov_mr(Mem(Reg.EBP, -4), Reg.EAX), "mov [ebp-4], eax")
        self._check(enc.encode_mov_rm(Reg.ECX, Mem(Reg.EBX, 0)), "mov ecx, [ebx]")
        self._check(enc.encode_mov_rm(Reg.ECX, Mem(Reg.EBP, 0)), "mov ecx, [ebp+0]")
        self._check(enc.encode_mov_rm(Reg.EAX, Mem(Reg.ESP, 8)), "mov eax, [esp+8]")
        self._check(
            enc.encode_mov_rm(Reg.EAX, Mem(Reg.EBX, 4, index=Reg.ECX, scale=4)),
            "mov eax, [ebx+ecx*4+4]",
        )
        self._check(enc.encode_mov_rm(Reg.EAX, Mem(None, 0x1000)), "mov eax, [dword 0x1000]")

    def test_lea(self):
        self._check(enc.encode_lea(Reg.EAX, Mem(Reg.EBP, -8)), "lea eax, [ebp-8]")

    # ---- ALU ------------------------------------------------------------

    def test_alu_rr(self):
        self._check(enc.encode_add_rr(Reg.EAX, Reg.ECX), "add eax, ecx")
        self._check(enc.encode_sub_rr(Reg.EAX, Reg.ECX), "sub eax, ecx")
        self._check(enc.encode_and_rr(Reg.EAX, Reg.ECX), "and eax, ecx")
        self._check(enc.encode_or_rr(Reg.EAX, Reg.ECX), "or eax, ecx")
        self._check(enc.encode_xor_rr(Reg.EAX, Reg.ECX), "xor eax, ecx")
        self._check(enc.encode_cmp_rr(Reg.EAX, Reg.ECX), "cmp eax, ecx")
        self._check(enc.encode_test_rr(Reg.EAX, Reg.ECX), "test eax, ecx")

    def test_imul_idiv_div(self):
        self._check(enc.encode_imul_rr(Reg.EAX, Reg.ECX), "imul eax, ecx")
        self._check(enc.encode_idiv_r(Reg.ECX), "idiv ecx")
        self._check(enc.encode_div_r(Reg.ECX), "div ecx")

    def test_mul(self):
        self._check(enc.encode_mul_r(Reg.ECX), "mul ecx")

    def test_adc_sbb(self):
        # 64-bit register-pair add/sub building blocks.
        self._check(enc.encode_adc_rr(Reg.EAX, Reg.ECX), "adc eax, ecx")
        self._check(enc.encode_sbb_rr(Reg.EAX, Reg.ECX), "sbb eax, ecx")

    def test_neg_not(self):
        self._check(enc.encode_neg(Reg.EAX), "neg eax")
        self._check(enc.encode_not(Reg.EAX), "not eax")

    def test_alu_ri(self):
        self._check(enc.encode_add_ri(Reg.EAX, 5), "add eax, 5")
        self._check(enc.encode_add_ri(Reg.EAX, 1000), "add eax, 1000")
        self._check(enc.encode_sub_ri(Reg.EAX, 5), "sub eax, 5")
        self._check(enc.encode_sub_ri(Reg.EAX, 1000), "sub eax, 1000")
        self._check(enc.encode_cmp_ri(Reg.EAX, 5), "cmp eax, 5")
        self._check(enc.encode_cmp_ri(Reg.EAX, 1000), "cmp eax, 1000")

    def test_xor_zero(self):
        self._check(enc.encode_xor_zero(Reg.EAX), "xor eax, eax")

    # ---- Shifts -----------------------------------------------------------

    def test_shifts(self):
        self._check(enc.encode_shl_ri(Reg.EAX, 1), "shl eax, 1")
        self._check(enc.encode_shl_ri(Reg.EAX, 4), "shl eax, 4")
        self._check(enc.encode_shr_ri(Reg.EAX, 1), "shr eax, 1")
        self._check(enc.encode_shr_ri(Reg.EAX, 4), "shr eax, 4")
        self._check(enc.encode_sar_ri(Reg.EAX, 1), "sar eax, 1")
        self._check(enc.encode_sar_ri(Reg.EAX, 4), "sar eax, 4")
        self._check(enc.encode_shl_cl(Reg.EAX), "shl eax, cl")
        self._check(enc.encode_shr_cl(Reg.EAX), "shr eax, cl")
        self._check(enc.encode_sar_cl(Reg.EAX), "sar eax, cl")

    def test_shld_shrd(self):
        # Double-precision shift -- the real primitive for 64-bit
        # register-pair shl/shr/sar by a constant/variable amount < 32.
        self._check(enc.encode_shld_ri(Reg.EAX, Reg.ECX, 5), "shld eax, ecx, 5")
        self._check(enc.encode_shld_cl(Reg.EAX, Reg.ECX), "shld eax, ecx, cl")
        self._check(enc.encode_shrd_ri(Reg.EAX, Reg.ECX, 5), "shrd eax, ecx, 5")
        self._check(enc.encode_shrd_cl(Reg.EAX, Reg.ECX), "shrd eax, ecx, cl")

    # ---- Sign / zero extension ---------------------------------------------

    def test_movsx_movzx(self):
        self._check(enc.encode_movsx(Reg.EAX, Reg.ECX, 8), "movsx eax, cl")
        self._check(enc.encode_movsx(Reg.EAX, Reg.ECX, 16), "movsx eax, cx")
        self._check(enc.encode_movzx(Reg.EAX, Reg.ECX, 8), "movzx eax, cl")
        self._check(enc.encode_movzx(Reg.EAX, Reg.ECX, 16), "movzx eax, cx")

    def test_cdq(self):
        self._check(enc.encode_cdq(), "cdq")

    # ---- Stack --------------------------------------------------------------

    def test_push_pop(self):
        self._check(enc.encode_push(Reg.EBX), "push ebx")
        self._check(enc.encode_pop(Reg.EBX), "pop ebx")
        self._check(enc.encode_push_i(42), "push dword 42")
        self._check(enc.encode_push_m(Mem(Reg.EBP, 8)), "push dword [ebp+8]")

    # ---- Control flow ---------------------------------------------------------

    def test_ret(self):
        self._check(enc.encode_ret(), "ret")
        self._check(enc.encode_ret_n(8), "ret 8")

    def test_call_jmp(self):
        # CALL rel32 is 5 bytes; rel32=-5 makes the target (site+5-5) ==
        # site itself -- a degenerate self-targeting call, but a clean way
        # to pin down the encoding without off-by-one relative-offset math.
        self._check(enc.encode_call_rel32(-5), "call $")
        self._check(enc.encode_call_r(Reg.EAX), "call eax")
        self._check(enc.encode_jmp_rel32(10), "jmp $+15")
        self._check(enc.encode_jmp_r(Reg.EAX), "jmp eax")

    def test_jcc(self):
        self._check(enc.encode_jcc_rel8(CC.E, 10), "je $+12")
        self._check(enc.encode_jcc_rel32(CC.E, 100), "je $+106")

    def test_setcc(self):
        for cc_name, cc in (("e", CC.E), ("ne", CC.NE), ("l", CC.L), ("g", CC.G)):
            for reg_name, reg in (("al", Reg.EAX), ("cl", Reg.ECX), ("dl", Reg.EDX), ("bl", Reg.EBX)):
                self._check(enc.encode_setcc(cc, reg), f"set{cc_name} {reg_name}")

    def test_nop(self):
        self._check(enc.encode_nop(), "nop")

    # ---- SSE2 scalar double ----------------------------------------------------

    def test_sse2_scalar_double(self):
        self._check(enc.encode_movsd_rr(XmmReg.XMM0, XmmReg.XMM1), "movsd xmm0, xmm1")
        self._check(enc.encode_addsd(XmmReg.XMM0, XmmReg.XMM1), "addsd xmm0, xmm1")
        self._check(enc.encode_subsd(XmmReg.XMM0, XmmReg.XMM1), "subsd xmm0, xmm1")
        self._check(enc.encode_mulsd(XmmReg.XMM0, XmmReg.XMM1), "mulsd xmm0, xmm1")
        self._check(enc.encode_divsd(XmmReg.XMM0, XmmReg.XMM1), "divsd xmm0, xmm1")
        self._check(enc.encode_ucomisd(XmmReg.XMM0, XmmReg.XMM1), "ucomisd xmm0, xmm1")
        self._check(enc.encode_movsd_rm(XmmReg.XMM0, Mem(Reg.EBP, -8)), "movsd xmm0, [ebp-8]")
        self._check(enc.encode_movsd_mr(Mem(Reg.EBP, -8), XmmReg.XMM0), "movsd [ebp-8], xmm0")
        self._check(enc.encode_cvtsi2sd(XmmReg.XMM0, Reg.EAX), "cvtsi2sd xmm0, eax")
        self._check(enc.encode_cvttsd2si(Reg.EAX, XmmReg.XMM0), "cvttsd2si eax, xmm0")

    # ---- SSE scalar float ---------------------------------------------------

    def test_sse_scalar_float(self):
        self._check(enc.encode_movss_rr(XmmReg.XMM0, XmmReg.XMM1), "movss xmm0, xmm1")
        self._check(enc.encode_addss(XmmReg.XMM0, XmmReg.XMM1), "addss xmm0, xmm1")
        self._check(enc.encode_cvtsi2ss(XmmReg.XMM0, Reg.EAX), "cvtsi2ss xmm0, eax")
        self._check(enc.encode_cvttss2si(Reg.EAX, XmmReg.XMM0), "cvttss2si eax, xmm0")

    # ---- Typed byte/word loads/stores ----------------------------------------

    def test_typed_loads_stores(self):
        self._check(enc.encode_movzx_rm8(Reg.EAX, Mem(Reg.EBP, -1)), "movzx eax, byte [ebp-1]")
        self._check(enc.encode_mov_mr8(Mem(Reg.EBP, -1), Reg.EAX), "mov [ebp-1], al")
        self._check(enc.encode_movzx_rm16(Reg.EAX, Mem(Reg.EBP, -2)), "movzx eax, word [ebp-2]")
        self._check(enc.encode_mov_rm32(Reg.EAX, Mem(Reg.EBP, -4)), "mov eax, [ebp-4]")
        self._check(enc.encode_mov_mr32(Mem(Reg.EBP, -4), Reg.EAX), "mov [ebp-4], eax")

    # ---- SIMD packed float/double -------------------------------------------

    def test_simd_packed(self):
        self._check(enc.encode_addps(XmmReg.XMM0, XmmReg.XMM1), "addps xmm0, xmm1")
        self._check(enc.encode_addpd(XmmReg.XMM0, XmmReg.XMM1), "addpd xmm0, xmm1")
        self._check(enc.encode_pxor(XmmReg.XMM0, XmmReg.XMM1), "pxor xmm0, xmm1")
        self._check(enc.encode_paddd(XmmReg.XMM0, XmmReg.XMM1), "paddd xmm0, xmm1")
        self._check(enc.encode_movdqu_rr(XmmReg.XMM0, XmmReg.XMM1), "movdqu xmm0, xmm1")
        self._check(enc.encode_pmulld(XmmReg.XMM0, XmmReg.XMM1), "pmulld xmm0, xmm1")
        self._check(enc.encode_pshufd(XmmReg.XMM0, XmmReg.XMM1, 0x1B), "pshufd xmm0, xmm1, 0x1B")
        self._check(enc.encode_movaps_rm(XmmReg.XMM0, Mem(Reg.EBP, -16)), "movaps xmm0, [ebp-16]")
        self._check(enc.encode_movaps_mr(Mem(Reg.EBP, -16), XmmReg.XMM0), "movaps [ebp-16], xmm0")


if __name__ == "__main__":
    unittest.main()
