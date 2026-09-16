"""Every AArch64 encoding, checked against `llvm-mc` word for word.

WHY THIS ORACLE. The same argument `test_x86_encode.py` makes -- an encoder is
a large table of small facts, and a hand-written expectation is the same fact
written twice by the same person -- plus one this architecture adds. There is
no AArch64 assembler on an x86 machine, so without a cross-assembler this file
could only run on the hardware it describes, which in practice means never.
`llvm-mc` assembles any target from any host.

WHAT MAKES AArch64 EASY TO GET WRONG. Every instruction is four bytes and
every field sits at a fixed position, so nothing here is malformed the way a
bad x86 prefix is. A misplaced field produces a DIFFERENT INSTRUCTION that
decodes cleanly and runs. Three found by this file:

  * `fcmp d0, d1` had the signalling bit set, so every float comparison was
    the trapping form.
  * `cset x0, eq` inverts its condition -- `csinc ..., ne` -- and passing the
    condition through unchanged makes every comparison in the program come
    out backwards.
  * `lsl x0, x1, #3` is not the shift instruction at all. The immediate forms
    are aliases of `ubfm`, so encoding one as the register form shifts by
    whatever register three holds.

`llvm-mc` IS NEEDED TO TEST THE ENCODER AND NOT TO USE IT. A machine without
it skips this file and still compiles for AArch64.
"""
from __future__ import annotations

import re
import struct
import subprocess

from tests import harness

from uasm.backends.arm64.encode import (
    EncodeError, Encoded, encode_function, encode_line,
)

TRIPLE = "aarch64-linux-gnu"


def assembled(lines: list[str]) -> list[tuple[int, int]]:
    """What `llvm-mc` makes of these lines: (word, mask) each.

    THE MASK IS THE POINT. `--show-encoding` writes a byte a relocation will
    fill in as `A`, and a byte only partly fixed up as `0x90'A'`. Those bits
    are not the encoder's to produce -- they are the linker's -- so they are
    masked out of the comparison rather than guessed at.
    """
    source = "\n".join("\t" + one for one in lines) + "\n"
    done = subprocess.run(
        ["llvm-mc", f"-triple={TRIPLE}", "--show-encoding"],
        input=source, capture_output=True, text=True)
    assert done.returncode == 0, f"llvm-mc refused:\n{done.stderr}"
    out = []
    for line in done.stdout.splitlines():
        found = re.search(r"//\s*encoding: \[(.*)\]\s*$", line)
        if found is None:
            continue
        word = mask = 0
        for index, byte in enumerate(found.group(1).split(",")):
            byte = byte.strip()
            if byte == "A":
                continue                        # the whole byte is a fixup
            if byte.startswith("0b"):
                value = kept = 0
                for bit in byte[2:]:
                    value = (value << 1) | (1 if bit == "1" else 0)
                    kept = (kept << 1) | (0 if bit == "A" else 1)
                word |= value << (8 * index)
                mask |= kept << (8 * index)
            elif "A" in byte:
                continue                        # partly a fixup: 0x90'A'
            else:
                word |= int(byte, 16) << (8 * index)
                mask |= 0xFF << (8 * index)
        out.append((word, mask))
    assert len(out) == len(lines), (
        f"llvm-mc answered {len(out)} encodings for {len(lines)} lines")
    return out


def ours(line: str) -> int:
    out = Encoded()
    encode_line(line, out)
    assert len(out.code) == 4, f"{line} encoded to {len(out.code)} bytes"
    return struct.unpack("<I", bytes(out.code))[0]


#: THE FORMS THE EMITTER PRODUCES, and the neighbours of each that are worth
#: distrusting. On AArch64 a neighbour is almost always about register 31,
#: which means `xzr` in one instruction and `sp` in another, or about the two
#: widths, since `w` and `x` differ by one bit and nothing else.
CASES = [
    # moves, and the three that are aliases of something else
    "mov x0, x1", "mov w0, w1", "mov x29, sp", "mov sp, x29",
    "mov x0, xzr", "mov x0, #0", "mov x0, #65535", "mov w3, w4",
    "movz x0, #0", "movz x0, #65535", "movz w1, #7",
    "movk x0, #16, lsl #16", "movk x0, #65535, lsl #48",
    "movk x1, #256, lsl #32", "movn x0, #0",
    # add and sub: register, immediate, the shifted immediate, and sp
    "add x0, x1, x2", "add w0, w1, w2", "add x0, x1, #0",
    "add x0, x1, #4095", "add x0, sp, #16", "add sp, sp, #112",
    "sub sp, sp, #112", "sub x0, x1, x4", "sub x0, x1, #1",
    "add x0, x1, #4096", "add x0, x1, #8192",
    "adds x0, x1, x2", "subs x0, x1, x2",
    "neg x1, x20", "neg w1, w20",
    "cmp x0, x1", "cmp w0, w1", "cmp x0, #0", "cmp x0, #255",
    # THE NEGATIVE IMMEDIATE, which is the other instruction: there is no sign
    # bit in the field, so `add #-8` must become `sub #8`.
    "add x0, x1, #-8", "sub x0, x1, #-8",
    # logical, register and immediate
    "and x0, x1, x15", "orr x0, x11, x1", "eor x0, x11, x14",
    "and x0, x0, #1", "and x0, x0, #255", "and w0, w0, #1",
    "and x0, x0, #4095", "orr x0, x0, #8",
    "mvn x14, x13", "tst x0, x1",
    # shifts, both by a register and by a constant -- different instructions
    "lsl x1, x15, x0", "lsr x10, x0, x9", "asr x10, x1, x4",
    "lsl x0, x1, #3", "lsr x0, x1, #3", "asr x0, x1, #3",
    "lsl w0, w1, #3", "lsr w0, w1, #31", "asr x0, x1, #63",
    "udiv x12, x14, x11", "sdiv x1, x19, x0", "sdiv w1, w19, w0",
    # multiply, and the three-source form the modulo lowering uses
    "mul x0, x12, x15", "mul w0, w12, w15",
    "msub x0, x16, x14, x13", "madd x0, x16, x14, x13",
    # extension
    "sxtw x1, w1", "sxtb x0, w0", "sxth x0, w0",
    "uxtb x0, w0", "uxth x0, w0", "uxtb w0, w0",
    # loads and stores, at every width and both addressing shapes
    "ldr x0, [sp, #16]", "ldr x0, [sp, #0]", "ldr x0, [x15]",
    "ldr w0, [x15]", "str x0, [sp, #0]", "str w12, [x4]",
    "ldrb w0, [x12]", "strb w0, [x12]", "ldrsw x0, [x14]",
    "ldrh w0, [x12]", "strh w0, [x12]", "ldrsb x0, [x12]",
    "ldr x0, [sp, #32760]", "str x0, [sp, #8]",
    # THE UNSCALED FORM, which a negative or unaligned offset needs.
    "ldr x0, [x1, #-8]", "str x0, [x1, #-8]", "ldr w0, [x1, #-4]",
    "ldr x0, [x1, #4]",
    # the frame pair, pre- and post-indexed
    "stp x29, x30, [sp, #-16]!", "ldp x29, x30, [sp], #16",
    "stp x29, x30, [sp, #16]", "ldp x0, x1, [sp, #-64]",
    # branches to a symbol, where only the fixed bits are compared
    "bl printf", "b somewhere", "blr x17", "br x17", "ret",
    "adrp x0, __rodata10", "add x0, x0, :lo12:__rodata10",
    # every condition, through cset -- the alias that inverts
    "cset x0, eq", "cset x0, ne", "cset x0, lt", "cset x0, le",
    "cset x0, gt", "cset x0, ge", "cset x0, hi", "cset x0, hs",
    "cset x0, lo", "cset x0, ls", "cset x0, mi", "cset x0, pl",
    "cset x0, vs", "cset x0, vc", "cset w0, eq",
    "csel x0, x1, x2, eq", "csinc x0, x1, x2, ne",
    # floating point
    "fadd d0, d0, d1", "fsub d0, d0, d1", "fmul d0, d0, d1",
    "fdiv d0, d0, d1", "fneg d0, d0", "fabs d0, d0", "fsqrt d0, d0",
    "fadd s0, s0, s1", "fmul s2, s3, s4",
    "fcmp d0, d1", "fcmp s0, s1", "fcmp d0, #0.0",
    "fmov d0, x16", "fmov x16, d0", "fmov s0, w16", "fmov w16, s0",
    "fcvtzs x0, d0", "fcvtzs w0, d0", "scvtf d0, x0", "scvtf d0, w0",
    "ucvtf d0, x0", "fcvtzu x0, d0",
    "fcvt s0, d0", "fcvt d0, s0",
    "ldr d0, [sp, #0]", "str d0, [sp, #8]", "ldr d0, [x0]",
    "str d0, [x0]", "ldr s0, [x0]", "ldr q0, [x0]",
    # the rest
    "nop", "brk #0", "brk #1",
]


@harness.needs("llvm-aarch64")
class TestEveryFormAgreesWithLlvm:
    """One case per encoding shape. See `CASES` for why the neighbours.

    ASKED IN ONE BATCH rather than one process per line: llvm-mc assembles
    two hundred lines as fast as one, and a hundred and eighty subprocesses is
    the difference between a test that runs on every change and one that does
    not.
    """

    def test_the_words_are_the_same(self):
        want = assembled(CASES)
        wrong = []
        for line, (word, mask) in zip(CASES, want):
            got = ours(line)
            if (got & mask) != (word & mask):
                wrong.append(f"{line}\n    ours {got:08x}\n"
                             f"    llvm {word:08x}   mask {mask:08x}")
        assert not wrong, "\n".join(wrong)


@harness.needs("llvm-aarch64")
class TestBranchesResolveWithinAFunction:
    """A branch to a block label is patched once every length is known.

    Compared against llvm-mc for the whole sequence rather than one line at a
    time, because a branch's encoding is not a property of its line: it is a
    property of where its target ended up.
    """

    SEQUENCES = [
        [".Ltop:", "add x0, x0, #1", "cmp x0, x1", "b.eq .Lend",
         "cbnz x2, .Ltop", "cbz x3, .Lend", "b .Ltop", ".Lend:", "ret"],
        ["b .Lonly", "nop", "nop", ".Lonly:", "ret"],
        [".Lback:", "nop", "b .Lback"],
        ["cbz w0, .Lout", "nop", ".Lout:", "ret"],
    ]

    @harness.cases("index", list(range(len(SEQUENCES))))
    def test_the_bytes_match_llvm(self, index, tmp_path):
        lines = self.SEQUENCES[index]
        source = "\n".join(one if one.endswith(":") else "\t" + one
                           for one in lines) + "\n"
        (tmp_path / "in.s").write_text(".text\n" + source, encoding="utf-8")
        subprocess.run(
            ["llvm-mc", f"-triple={TRIPLE}", "-filetype=obj",
             "-o", str(tmp_path / "in.o"), str(tmp_path / "in.s")],
            check=True, capture_output=True)
        subprocess.run(
            ["llvm-objcopy", "-O", "binary", "--only-section=.text",
             str(tmp_path / "in.o"), str(tmp_path / "in.bin")],
            check=True, capture_output=True)
        want = (tmp_path / "in.bin").read_bytes()
        got = bytes(encode_function(lines).code)
        assert got == want, f"ours {got.hex(' ')}\nllvm {want.hex(' ')}"

    def test_an_unknown_label_is_refused(self):
        out = Encoded()
        encode_line("b .Lnowhere", out)
        with harness.raises(EncodeError, match="Lnowhere"):
            from uasm.backends.arm64.encode import resolve
            resolve(out)


class TestACallOutOfTheFunctionAsksForARelocation:
    """A symbol this function does not define is the linker's to place."""

    def test_a_call_names_the_symbol(self):
        from uasm.backends.arm64.encode import R_AARCH64_CALL26
        out = Encoded()
        encode_line("bl apy_from_int", out)
        assert out.relocs == [(0, "apy_from_int", R_AARCH64_CALL26, 0)]

    def test_the_address_of_a_global_takes_two(self):
        """`adrp` then `add`, which is how AArch64 spells a 32-bit offset.

        BOTH HALVES NEED A RELOCATION and they are different ones: the page
        and the offset within it are computed separately, so a backend
        emitting only the first would land on the right page and the wrong
        variable.
        """
        from uasm.backends.arm64.encode import (
            R_AARCH64_ADD_ABS_LO12_NC, R_AARCH64_ADR_PREL_PG_HI21,
        )
        out = Encoded()
        encode_line("adrp x0, gv_x", out)
        encode_line("add x0, x0, :lo12:gv_x", out)
        assert out.relocs == [
            (0, "gv_x", R_AARCH64_ADR_PREL_PG_HI21, 0),
            (4, "gv_x", R_AARCH64_ADD_ABS_LO12_NC, 0)]


class TestWhatItCannotEncodeItRefuses:
    """A guess would be a program that runs and does something else."""

    def test_an_unknown_mnemonic_names_itself(self):
        out = Encoded()
        with harness.raises(EncodeError, match="fmla"):
            encode_line("fmla v0.4s, v1.4s, v2.4s", out)

    def test_an_immediate_no_logical_form_holds_is_refused(self):
        """A logical immediate is a rotated run of ones, not a number.

        `#5` is `0b101`, which no rotation of a run of ones produces, so there
        is no encoding -- and llvm-mc refuses it too. Truncating would mask
        with a different value on every use.
        """
        out = Encoded()
        with harness.raises(EncodeError, match="logical immediate"):
            encode_line("and x0, x0, #5", out)

    def test_an_immediate_too_wide_for_add_is_refused(self):
        """`add` carries twelve bits, optionally shifted by twelve. A value
        needing bits in between has no encoding at all."""
        out = Encoded()
        with harness.raises(EncodeError, match="cannot carry"):
            encode_line("add x0, x1, #4097", out)

    def test_a_move_of_more_than_sixteen_bits_is_refused(self):
        """One `movz` reaches sixteen bits at one of four positions. Anything
        else is several instructions, which is the emitter's decision to make
        and not something to invent here."""
        out = Encoded()
        with harness.raises(EncodeError, match="more than one instruction"):
            encode_line("mov x0, #65537", out)
