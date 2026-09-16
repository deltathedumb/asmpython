"""AArch64 instruction encoding: the stage that used to be an assembler.

WHY THIS EXISTS. The same argument `backends/x86_64/encode.py` makes: a
backend emitting `.s` has never decided a byte of its output, so it cannot
produce a program on a machine without an assembler for the target. This is
that file's counterpart, and it is a much smaller one.

WHY IT IS SMALLER. Every AArch64 instruction is exactly four bytes and every
field sits at a fixed bit position. There is no prefix to decide, no ModRM,
no SIB, no variable-length displacement -- the whole class of x86 bugs about
which register needs which escape byte does not exist here. What replaces it
is FIELD PACKING: an encoding is a handful of integers dropped into named bit
ranges, and getting one range wrong produces a different instruction rather
than a malformed one. So the risk moves from "will it decode" to "is it the
instruction I meant", which is exactly what a differential test answers.

HOW IT IS KNOWN TO BE RIGHT. Every form is compared against `llvm-mc`, word
for word, in `tests/uasm/unit/test_arm64_encode.py` -- and, unlike the
x86 side, over EVERY DISTINCT LINE the emitter produces across the whole
end-to-end corpus, because there are only about fifty shapes and llvm-mc will
assemble a thousand lines as fast as one. llvm-mc is needed to TEST this file
and not to USE it.

THE OPERAND FORMS the emitter produces, and nothing else:

    x0  w12  xzr  wzr  sp        a general register, at one of two widths
    d0  s3                       a floating-point register
    #16  #-8                     an immediate
    [x15]   [sp, #16]            base, with an optional scaled offset
    [sp, #-16]!   [sp], #16      pre- and post-indexed, for the frame pair
    sym   :lo12:sym              a symbol, whole or its low twelve bits
    .Lname                       a branch target inside this function
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

# ── relocation numbers, from the AAELF64 psABI ──────────────────────────────
#
# NOT INTERPRETED HERE beyond being written down, for the reason the x86 file
# gives: what a linker does with one is its business, and they are the
# ARCHITECTURE's rather than the format's.
R_AARCH64_ABS64 = 257
R_AARCH64_ADR_PREL_PG_HI21 = 275
R_AARCH64_ADD_ABS_LO12_NC = 277
R_AARCH64_JUMP26 = 282
R_AARCH64_CALL26 = 283
R_AARCH64_LDST8_ABS_LO12_NC = 278
R_AARCH64_LDST16_ABS_LO12_NC = 284
R_AARCH64_LDST32_ABS_LO12_NC = 285
R_AARCH64_LDST64_ABS_LO12_NC = 286


class EncodeError(Exception):
    """A line this file cannot encode. Never a guess -- see the module docstring."""


# ── operands ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reg:
    """One register. `num` 31 is `xzr`/`wzr` or `sp` DEPENDING ON THE
    INSTRUCTION, which is a genuine ambiguity in the architecture rather than
    a shortcut here: the same five bits mean the zero register in `add x0, x1,
    x2` and the stack pointer in `add x0, sp, #16`. `is_sp` records which
    spelling was written so an encoder that cares can ask."""

    name: str
    num: int
    bits: int
    kind: str = "gpr"                   # "gpr" or "fp"

    @property
    def is_sp(self) -> bool:
        return self.name == "sp"

    @property
    def sf(self) -> int:
        """The width bit: 1 for a 64-bit operation, 0 for 32."""
        return 1 if self.bits == 64 else 0


@dataclass(frozen=True)
class Imm:
    value: int


@dataclass(frozen=True)
class Mem:
    """`[base]`, `[base, #off]`, `[base, #off]!` or `[base], #off`."""

    base: Reg
    offset: int = 0
    #: "none" for a plain offset, "pre" for `[b, #o]!`, "post" for `[b], #o`.
    index: str = "none"


@dataclass(frozen=True)
class Shift:
    """`lsl #16` written as an operand, which is what `movk` takes."""

    kind: str
    amount: int


@dataclass(frozen=True)
class Label:
    """A branch target inside this function -- patched by `resolve`."""

    name: str


@dataclass(frozen=True)
class SymRef:
    """A symbol, and which part of its address this line wants.

    `part` is "page" for `adrp`, "lo12" for `:lo12:`, and "whole" for a branch
    target. Each becomes a different relocation, which is the only reason the
    distinction is carried this far.
    """

    name: str
    part: str


Operand = Reg | Imm | Mem | Label | SymRef | Shift


# ── the register names the emitter writes ───────────────────────────────────

def _register(text: str) -> Reg | None:
    if text == "sp":
        return Reg("sp", 31, 64)
    if text == "wsp":
        return Reg("wsp", 31, 32)
    if text in ("xzr", "wzr"):
        return Reg(text, 31, 64 if text[0] == "x" else 32)
    match = re.fullmatch(r"([xwdsqhb])(\d{1,2})", text)
    if match is None:
        return None
    letter, number = match.group(1), int(match.group(2))
    if number > 30 and letter in "xw":
        return None
    if number > 31:
        return None
    if letter == "x":
        return Reg(text, number, 64)
    if letter == "w":
        return Reg(text, number, 32)
    width = {"b": 8, "h": 16, "s": 32, "d": 64, "q": 128}[letter]
    return Reg(text, number, width, "fp")


def _parse_operand(text: str) -> Operand:
    text = text.strip()
    reg = _register(text)
    if reg is not None:
        return reg
    if text.startswith("#"):
        return Imm(_number(text[1:]))
    if text.startswith(":lo12:"):
        return SymRef(text[6:], "lo12")
    if text.startswith("["):
        return _parse_mem(text)
    shift = re.fullmatch(r"(lsl|lsr|asr|ror)\s+#(-?\w+)", text, re.IGNORECASE)
    if shift is not None:
        return Shift(shift.group(1).lower(), _number(shift.group(2)))
    if text.startswith("."):
        return Label(text)
    if re.fullmatch(r"[A-Za-z_.$][\w.$]*", text):
        return SymRef(text, "whole")
    raise EncodeError(f"cannot parse operand {text!r}")


def _number(text: str) -> int:
    """An immediate's value. A float spelling is allowed only where it means zero.

    `fcmp d0, #0.0` IS THE ONLY FLOATING-POINT IMMEDIATE in this subset, and
    it is not a value in a field at all -- it selects a different comparison
    instruction. Any other float would need the eight-bit `fmov` immediate
    encoding, which nothing emits, so it is refused rather than rounded.
    """
    text = text.strip()
    if "." in text:
        value = float(text)
        if value != 0.0:
            raise EncodeError(
                f"{text} is not an encodable floating-point immediate; only "
                f"zero is, and only in a comparison")
        return 0
    return int(text, 16) if text.lower().startswith(("0x", "-0x")) else int(text, 0)


def _parse_mem(text: str) -> Mem:
    """`[x0]`, `[sp, #16]`, `[sp, #-16]!` and `[sp], #16`.

    THE BRACKET IS NOT ENOUGH TO SPLIT ON: a post-indexed operand has its
    offset OUTSIDE the brackets, which is the whole notational difference
    between writing the address back and not. Splitting on the closing bracket
    rather than scanning to the end is what keeps the two apart.
    """
    close = text.index("]")
    inside, after = text[1:close], text[close + 1:].strip()
    after = after.replace(" ", "")
    parts = [p.strip() for p in inside.split(",")]
    base = _register(parts[0])
    if base is None:
        raise EncodeError(f"cannot parse memory base in {text!r}")
    if after.startswith(","):
        return Mem(base, _number(after[1:].strip().lstrip("#")), "post")
    offset = _number(parts[1].lstrip("#")) if len(parts) > 1 else 0
    return Mem(base, offset, "pre" if after == "!" else "none")


# ── condition codes ─────────────────────────────────────────────────────────

_CC = {"eq": 0, "ne": 1, "cs": 2, "hs": 2, "cc": 3, "lo": 3, "mi": 4, "pl": 5,
       "vs": 6, "vc": 7, "hi": 8, "ls": 9, "ge": 10, "lt": 11, "gt": 12,
       "le": 13, "al": 14, "nv": 15}


# ── what one function's encoding accumulates ────────────────────────────────

@dataclass
class Fixup:
    """A branch whose target is not encoded yet. `at` is the word's offset."""

    at: int
    label: str
    #: "b26" for an unconditional branch, "b19" for a compare-and-branch.
    width: str


@dataclass
class Encoded:
    code: bytearray = field(default_factory=bytearray)
    #: (offset, symbol, relocation number, addend)
    relocs: list[tuple[int, str, int, int]] = field(default_factory=list)
    fixups: list[Fixup] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)


def _word(out: Encoded, value: int) -> None:
    out.code += struct.pack("<I", value & 0xFFFFFFFF)


# ── bit-field helpers ───────────────────────────────────────────────────────
#
# EVERY ENCODER BELOW IS ONE OF THESE PLUS ITS OPCODE BITS. Naming them makes
# the encodings read as the manual writes them rather than as a column of
# shifts, and it puts the "did I use the right range" question in one place.

def _fits_unsigned(value: int, bits: int) -> bool:
    return 0 <= value < (1 << bits)


def _fits_signed(value: int, bits: int) -> bool:
    return -(1 << (bits - 1)) <= value < (1 << (bits - 1))


def _bitmask_immediate(value: int, bits: int) -> int | None:
    """`N:immr:imms` for a logical immediate, or None if it is not one.

    THE ONE GENUINELY AWKWARD ENCODING IN THE ARCHITECTURE. A logical
    immediate is not a number in a field: it is a repeating bit pattern
    described by an element size, a run length and a rotation, which is why
    `and x0, x0, #1` encodes and `and x0, x0, #3145731` does not. Rather than
    invert the description, this SEARCHES the (size, length, rotation) space
    and takes the first triple that reproduces the value -- 2,048 candidates
    at worst, which is nothing next to being wrong.
    """
    value &= (1 << bits) - 1
    if value == 0 or value == (1 << bits) - 1:
        return None                     # neither is representable
    for size in (2, 4, 8, 16, 32, 64):
        if size > bits:
            break
        mask = (1 << size) - 1
        element = value & mask
        # THE PATTERN MUST REPEAT at this element size, or this size is wrong.
        if any((value >> shift) & mask != element
               for shift in range(size, bits, size)):
            continue
        for rotation in range(size):
            rotated = ((element >> rotation)
                       | (element << (size - rotation))) & mask
            # A run of ones starting at bit zero is what the field describes;
            # the rotation is how it got there.
            ones = rotated.bit_length()
            if rotated != (1 << ones) - 1 or ones == size:
                continue
            # `immr` IS THE RIGHT ROTATION THE HARDWARE APPLIES, and the
            # loop above found the LEFT one -- it rotated the value right
            # until the run reached bit zero. Writing the loop's variable
            # straight into the field is right only when the rotation is
            # zero, which is every logical immediate the emitter happens to
            # produce, so nothing but a neighbour case would catch it.
            immr = (size - rotation) % size
            imms = (((-size) << 1) & 0x3F) | (ones - 1)
            n = 1 if size == 64 else 0
            return (n << 12) | (immr << 6) | imms
    return None


# ── the instruction table ───────────────────────────────────────────────────

#: Data-processing, two source registers: `sf 0 0 11010110 Rm opcode Rn Rd`.
_DP2 = {"udiv": 0b000010, "sdiv": 0b000011, "lsl": 0b001000, "lslv": 0b001000,
        "lsr": 0b001001, "lsrv": 0b001001, "asr": 0b001010, "asrv": 0b001010,
        "ror": 0b001011, "rorv": 0b001011}

#: Logical, shifted register: opc and the N bit that distinguishes the
#: inverted form (`bic`, `orn`, `eon`) from the plain one.
_LOGICAL = {"and": (0b00, 0), "bic": (0b00, 1), "orr": (0b01, 0),
            "orn": (0b01, 1), "eor": (0b10, 0), "eon": (0b10, 1),
            "ands": (0b11, 0), "bics": (0b11, 1)}

#: Floating-point, two source registers: the `opcode` field at bits 15:12.
_FP2 = {"fmul": 0b0000, "fdiv": 0b0001, "fadd": 0b0010, "fsub": 0b0011,
        "fmax": 0b0100, "fmin": 0b0101}

#: Floating-point, one source register: the `opcode` field at bits 20:15.
_FP1 = {"fmov": 0b000000, "fabs": 0b000001, "fneg": 0b000010,
        "fsqrt": 0b000011}

#: The `ftype` field: which floating-point width this is.
_FTYPE = {32: 0b00, 64: 0b01, 16: 0b11}

#: Load and store, by mnemonic: (size field, V bit, opc field, access bits).
#: `access` is what the offset is scaled by and which `:lo12:` relocation the
#: line would need.
_LDST = {
    "ldr": None,                        # width comes from the register
    "str": None,
    "ldrb": (0b00, 0, 0b01, 8), "strb": (0b00, 0, 0b00, 8),
    "ldrh": (0b01, 0, 0b01, 16), "strh": (0b01, 0, 0b00, 16),
    "ldrsw": (0b10, 0, 0b10, 32),
    # THE SIGN-EXTENDING BYTE AND HALFWORD LOADS TAKE THEIR `opc` FROM THE
    # DESTINATION, not from the access width: 10 extends into a 64-bit
    # register and 11 into a 32-bit one. `None` here means the same thing it
    # means for `ldr` -- ask the register -- and the loader fills it in.
    "ldrsb": (0b00, 0, None, 8), "ldrsh": (0b01, 0, None, 16),
}

#: `:lo12:` relocation by access width, for a load or store of a symbol.
_LDST_LO12 = {8: R_AARCH64_LDST8_ABS_LO12_NC,
              16: R_AARCH64_LDST16_ABS_LO12_NC,
              32: R_AARCH64_LDST32_ABS_LO12_NC,
              64: R_AARCH64_LDST64_ABS_LO12_NC}


def _split(line: str) -> tuple[str, list[str]]:
    """Mnemonic and operand texts. Commas inside brackets do not separate."""
    text = line.split("//")[0].strip()
    head, _, rest = text.partition(" ")
    operands, depth, current = [], 0, ""
    for char in rest:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        if char == "," and depth == 0:
            operands.append(current)
            current = ""
            continue
        current += char
    if current.strip():
        operands.append(current)
    operands = [o.strip() for o in operands if o.strip()]
    # A POST-INDEXED OPERAND SPLIT IN TWO. `ldp x29, x30, [sp], #16` has its
    # offset outside the brackets -- that is exactly what distinguishes it
    # from `[sp, #16]`, which does not write the base back. The comma before
    # it is at depth zero, so the scan above cannot help; rejoining here is
    # unambiguous because the memory operand is always last.
    if len(operands) >= 2 and operands[-1].startswith("#") \
            and operands[-2].endswith("]"):
        operands[-2:] = [operands[-2] + ", " + operands[-1]]
    return head.lower(), operands


def encode_line(line: str, out: Encoded) -> None:
    """One line, appended to `out`. A label records its offset and emits nothing."""
    text = line.split("//")[0].strip()
    if not text or text.startswith("#"):
        return
    if text.endswith(":"):
        out.labels[text[:-1]] = len(out.code)
        return
    mnemonic, operand_text = _split(text)
    ops = [_parse_operand(one) for one in operand_text]
    _encode(mnemonic, ops, operand_text, out)


def _encode(mnemonic: str, ops: list[Operand], raw: list[str],
            out: Encoded) -> None:
    at = len(out.code)

    # ── branches ────────────────────────────────────────────────────────────
    if mnemonic in ("b", "bl"):
        opcode = 0b100101 if mnemonic == "bl" else 0b000101
        target = ops[0]
        if isinstance(target, Label):
            out.fixups.append(Fixup(at, target.name, "b26"))
            _word(out, opcode << 26)
            return
        assert isinstance(target, SymRef)
        # A CALL OUT OF THIS FUNCTION is the linker's to place. `bl` and a
        # taken `b` to another function use the same 26-bit field and differ
        # only in which relocation names it.
        out.relocs.append((at, target.name,
                           R_AARCH64_CALL26 if mnemonic == "bl"
                           else R_AARCH64_JUMP26, 0))
        _word(out, opcode << 26)
        return
    if mnemonic in ("cbz", "cbnz"):
        reg, target = ops
        assert isinstance(reg, Reg) and isinstance(target, Label)
        out.fixups.append(Fixup(at, target.name, "b19"))
        _word(out, (reg.sf << 31) | (0b011010 << 25)
              | ((1 if mnemonic == "cbnz" else 0) << 24) | reg.num)
        return
    if mnemonic.startswith("b.") and mnemonic[2:] in _CC:
        target = ops[0]
        assert isinstance(target, Label)
        out.fixups.append(Fixup(at, target.name, "b19"))
        _word(out, (0b0101010 << 25) | _CC[mnemonic[2:]])
        return
    if mnemonic in ("br", "blr"):
        reg = ops[0]
        assert isinstance(reg, Reg)
        _word(out, (0b1101011000 << 22)
              | ((1 if mnemonic == "blr" else 0) << 21)
              | (0b11111 << 16) | (reg.num << 5))
        return
    if mnemonic == "ret":
        reg = ops[0] if ops else Reg("x30", 30, 64)
        assert isinstance(reg, Reg)
        _word(out, 0xD65F0000 | (reg.num << 5))
        return
    if mnemonic in ("nop", "brk"):
        if mnemonic == "nop":
            _word(out, 0xD503201F)
        else:
            value = ops[0] if ops else Imm(0)
            assert isinstance(value, Imm)
            _word(out, 0xD4200000 | ((value.value & 0xFFFF) << 5))
        return

    # ── adrp ────────────────────────────────────────────────────────────────
    if mnemonic == "adrp":
        dst, target = ops
        assert isinstance(dst, Reg) and isinstance(target, SymRef)
        out.relocs.append((at, target.name, R_AARCH64_ADR_PREL_PG_HI21, 0))
        _word(out, (1 << 31) | (0b10000 << 24) | dst.num)
        return

    # ── moves ───────────────────────────────────────────────────────────────
    if mnemonic in ("movz", "movk", "movn", "mov"):
        return _move(mnemonic, ops, raw, out)

    # ── add and sub ─────────────────────────────────────────────────────────
    if mnemonic in ("add", "adds", "sub", "subs", "cmp", "cmn", "neg", "negs"):
        return _addsub(mnemonic, ops, out)

    # ── logical ─────────────────────────────────────────────────────────────
    if mnemonic in _LOGICAL or mnemonic in ("mvn", "tst"):
        return _logical(mnemonic, ops, out)

    # ── two-source data processing ──────────────────────────────────────────
    if mnemonic in _DP2:
        dst, lhs, rhs = ops
        assert isinstance(dst, Reg) and isinstance(lhs, Reg)
        if isinstance(rhs, Imm):
            # `lsl x0, x1, #3` is not this instruction at all: the immediate
            # forms are aliases of UBFM, which is a different encoding.
            return _shift_immediate(mnemonic, dst, lhs, rhs, out)
        assert isinstance(rhs, Reg)
        _word(out, (dst.sf << 31) | (0b0011010110 << 21) | (rhs.num << 16)
              | (_DP2[mnemonic] << 10) | (lhs.num << 5) | dst.num)
        return

    # ── three-source: mul, msub, madd ───────────────────────────────────────
    if mnemonic in ("mul", "madd", "msub", "mneg"):
        if mnemonic in ("mul", "mneg"):
            dst, lhs, rhs = ops
            addend = Reg("xzr", 31, dst.bits)       # type: ignore[union-attr]
        else:
            dst, lhs, rhs, addend = ops
        assert isinstance(dst, Reg) and isinstance(lhs, Reg)
        assert isinstance(rhs, Reg) and isinstance(addend, Reg)
        o0 = 1 if mnemonic in ("msub", "mneg") else 0
        _word(out, (dst.sf << 31) | (0b0011011000 << 21) | (rhs.num << 16)
              | (o0 << 15) | (addend.num << 10) | (lhs.num << 5) | dst.num)
        return

    # ── conditional select ──────────────────────────────────────────────────
    if mnemonic in ("cset", "csetm", "csinc", "csel", "csinv", "cneg"):
        return _conditional(mnemonic, ops, raw, out)

    # ── sign and zero extension ─────────────────────────────────────────────
    if mnemonic in ("sxtb", "sxth", "sxtw", "uxtb", "uxth"):
        dst, src = ops
        assert isinstance(dst, Reg) and isinstance(src, Reg)
        imms = {"b": 7, "h": 15, "w": 31}[mnemonic[3]]
        signed = mnemonic[0] == "s"
        # UXTB AND UXTH ARE 32-BIT ALIASES whatever register was written:
        # `uxtb x0, w0` assembles as `uxtb w0, w0`, because zero-extending
        # into the bottom 32 bits already clears the top ones.
        sf = dst.sf if signed else 0
        n = sf
        opc = 0b00 if signed else 0b10
        _word(out, (sf << 31) | (opc << 29) | (0b100110 << 23) | (n << 22)
              | (0 << 16) | (imms << 10) | (src.num << 5) | dst.num)
        return
    if mnemonic in ("sbfx", "ubfx", "sbfiz", "ubfiz", "lsr", "asr"):
        raise EncodeError(f"{mnemonic!r} in a form this encoder does not take")

    # ── loads and stores ────────────────────────────────────────────────────
    if mnemonic in _LDST:
        return _loadstore(mnemonic, ops, out)
    if mnemonic in ("ldp", "stp"):
        return _pair(mnemonic, ops, out)

    # ── floating point ──────────────────────────────────────────────────────
    got = _float(mnemonic, ops, out)
    if got:
        return
    raise EncodeError(f"cannot encode {mnemonic!r}")


def _move(mnemonic: str, ops: list[Operand], raw: list[str],
          out: Encoded) -> None:
    dst = ops[0]
    assert isinstance(dst, Reg)
    if mnemonic == "mov":
        src = ops[1]
        if isinstance(src, Reg):
            if dst.is_sp or src.is_sp:
                # `mov x29, sp` IS `add x29, sp, #0`, and must be: the ORR
                # alias below cannot name the stack pointer at all, because
                # register 31 means the zero register there.
                _word(out, (dst.sf << 31) | (0b00100010 << 23)
                      | (src.num << 5) | dst.num)
                return
            # `mov Xd, Xm` is `orr Xd, xzr, Xm`.
            _word(out, (dst.sf << 31) | (0b01 << 29) | (0b01010 << 24)
                  | (src.num << 16) | (31 << 5) | dst.num)
            return
        assert isinstance(src, Imm)
        packed = _movz_fields(src.value, dst.bits)
        if packed is None:
            raise EncodeError(
                f"mov of {src.value} needs more than one instruction; the "
                f"emitter must write movz and movk itself")
        hw, imm16 = packed
        _word(out, (dst.sf << 31) | (0b10 << 29) | (0b100101 << 23)
              | (hw << 21) | (imm16 << 5) | dst.num)
        return

    value = ops[1]
    assert isinstance(value, Imm)
    hw = 0
    if len(ops) > 2:
        # `movk x0, #16, lsl #16`. The shift is in units of sixteen bits,
        # which is the only thing the two-bit field can say.
        shift = ops[2]
        if not isinstance(shift, Shift) or shift.kind != "lsl":
            raise EncodeError(f"a move takes only `lsl`, not {raw[2]!r}")
        if shift.amount % 16 or not 0 <= shift.amount <= 48:
            raise EncodeError(f"a move cannot shift by {shift.amount}")
        hw = shift.amount // 16
    opc = {"movn": 0b00, "movz": 0b10, "movk": 0b11}[mnemonic]
    if not _fits_unsigned(value.value & 0xFFFF, 16) or \
            not (-(1 << 16) < value.value < (1 << 16)):
        raise EncodeError(f"{mnemonic} takes sixteen bits, not {value.value}")
    _word(out, (dst.sf << 31) | (opc << 29) | (0b100101 << 23) | (hw << 21)
          | ((value.value & 0xFFFF) << 5) | dst.num)


def _movz_fields(value: int, bits: int) -> tuple[int, int] | None:
    """`hw` and the sixteen bits, when one `movz` is enough."""
    if value < 0:
        return None
    for hw in range(bits // 16):
        if value == (value & (0xFFFF << (hw * 16))):
            return hw, (value >> (hw * 16)) & 0xFFFF
    return None


def _addsub(mnemonic: str, ops: list[Operand], out: Encoded) -> None:
    """add, sub and the aliases that are really one of them.

    `cmp` IS `subs xzr, ...` and `neg` IS `sub Rd, xzr, ...`. Encoding them
    as aliases rather than as separate entries is not a shortcut: it is what
    the architecture says they are, and writing them out separately would be
    two more chances to put a bit in the wrong place.
    """
    if mnemonic in ("cmp", "cmn"):
        lhs = ops[0]
        assert isinstance(lhs, Reg)
        dst = Reg("xzr", 31, lhs.bits)
        ops = [dst, lhs, ops[1]]
        mnemonic = "subs" if mnemonic == "cmp" else "adds"
    elif mnemonic in ("neg", "negs"):
        dst = ops[0]
        assert isinstance(dst, Reg)
        ops = [dst, Reg("xzr", 31, dst.bits), ops[1]]
        mnemonic = "subs" if mnemonic == "negs" else "sub"

    dst, lhs, rhs = ops[0], ops[1], ops[2]
    assert isinstance(dst, Reg) and isinstance(lhs, Reg)
    op = 1 if mnemonic.startswith("sub") else 0
    s = 1 if mnemonic.endswith("s") else 0

    if isinstance(rhs, SymRef):
        # `add x0, x0, :lo12:sym` -- the second half of an `adrp` pair, and
        # the immediate is the linker's to fill in.
        out.relocs.append((len(out.code), rhs.name,
                           R_AARCH64_ADD_ABS_LO12_NC, 0))
        _word(out, (dst.sf << 31) | (op << 30) | (s << 29) | (0b100010 << 23)
              | (lhs.num << 5) | dst.num)
        return
    if isinstance(rhs, Imm):
        value, shift = rhs.value, 0
        if value < 0:
            # A NEGATIVE IMMEDIATE IS THE OTHER INSTRUCTION. There is no sign
            # bit in the field, so `add x0, x0, #-8` is `sub x0, x0, #8` --
            # which llvm-mc also does, and which an encoder truncating instead
            # would turn into an addition of four thousand and eighty-eight.
            value, op = -value, 1 - op
        if value >= (1 << 12):
            if value & 0xFFF or value >= (1 << 24):
                raise EncodeError(
                    f"{mnemonic} of {rhs.value} needs an immediate this "
                    f"instruction cannot carry")
            value, shift = value >> 12, 1
        _word(out, (dst.sf << 31) | (op << 30) | (s << 29) | (0b100010 << 23)
              | (shift << 22) | (value << 10) | (lhs.num << 5) | dst.num)
        return

    assert isinstance(rhs, Reg)
    if dst.is_sp or lhs.is_sp:
        # The extended-register form, which is what `add sp, sp, x0` needs;
        # the shifted form cannot name the stack pointer.
        option = 0b011 if dst.bits == 64 else 0b010
        _word(out, (dst.sf << 31) | (op << 30) | (s << 29) | (0b01011001 << 21)
              | (rhs.num << 16) | (option << 13) | (lhs.num << 5) | dst.num)
        return
    _word(out, (dst.sf << 31) | (op << 30) | (s << 29) | (0b01011 << 24)
          | (rhs.num << 16) | (lhs.num << 5) | dst.num)


def _logical(mnemonic: str, ops: list[Operand], out: Encoded) -> None:
    if mnemonic == "mvn":
        dst = ops[0]
        assert isinstance(dst, Reg)
        ops = [dst, Reg("xzr", 31, dst.bits), ops[1]]
        mnemonic = "orn"
    elif mnemonic == "tst":
        lhs = ops[0]
        assert isinstance(lhs, Reg)
        ops = [Reg("xzr", 31, lhs.bits), lhs, ops[1]]
        mnemonic = "ands"

    dst, lhs, rhs = ops[0], ops[1], ops[2]
    assert isinstance(dst, Reg) and isinstance(lhs, Reg)
    opc, n = _LOGICAL[mnemonic]

    if isinstance(rhs, Imm):
        if n:
            raise EncodeError(f"{mnemonic} has no immediate form")
        packed = _bitmask_immediate(rhs.value, dst.bits)
        if packed is None:
            raise EncodeError(
                f"{rhs.value} is not a logical immediate: no rotation of a "
                f"run of ones produces it")
        if dst.bits == 32 and packed >> 12:
            raise EncodeError(f"{rhs.value} needs a 64-bit logical immediate")
        _word(out, (dst.sf << 31) | (opc << 29) | (0b100100 << 23)
              | (packed << 10) | (lhs.num << 5) | dst.num)
        return

    assert isinstance(rhs, Reg)
    _word(out, (dst.sf << 31) | (opc << 29) | (0b01010 << 24) | (n << 21)
          | (rhs.num << 16) | (lhs.num << 5) | dst.num)


def _shift_immediate(mnemonic: str, dst: Reg, src: Reg, amount: Imm,
                     out: Encoded) -> None:
    """`lsl`, `lsr` and `asr` by a constant, which are bitfield moves.

    NOT THE INSTRUCTION THE MNEMONIC NAMES. `lsl x0, x1, #3` is `ubfm x0, x1,
    #61, #60`: a shift by a register and a shift by a constant are different
    encodings with one spelling, and taking the register form's opcode with an
    immediate in it would produce a shift by whatever register three is.
    """
    width = dst.bits
    shift = amount.value
    if not 0 <= shift < width:
        raise EncodeError(f"a {width}-bit shift by {shift}")
    n = dst.sf
    if mnemonic in ("lsl", "lslv"):
        immr, imms, opc = (-shift) % width, width - 1 - shift, 0b10
    elif mnemonic in ("lsr", "lsrv"):
        immr, imms, opc = shift, width - 1, 0b10
    elif mnemonic in ("asr", "asrv"):
        immr, imms, opc = shift, width - 1, 0b00
    else:
        raise EncodeError(f"{mnemonic!r} takes no immediate")
    _word(out, (dst.sf << 31) | (opc << 29) | (0b100110 << 23) | (n << 22)
          | (immr << 16) | (imms << 10) | (src.num << 5) | dst.num)


def _conditional(mnemonic: str, ops: list[Operand], raw: list[str],
                 out: Encoded) -> None:
    """`cset` and friends. The condition arrives as a bare word, not an operand.

    THE CONDITION IS INVERTED for `cset`, which is the detail worth naming:
    `cset x0, eq` is `csinc x0, xzr, xzr, ne`, so an encoder passing the
    condition through unchanged produces a flag that is right exactly when it
    should be wrong -- and every comparison in the program comes out negated.
    """
    condition_text = raw[-1].strip().lower()
    if condition_text not in _CC:
        raise EncodeError(f"unknown condition {condition_text!r}")
    condition = _CC[condition_text]
    dst = ops[0]
    assert isinstance(dst, Reg)
    if mnemonic in ("cset", "csetm"):
        zero = Reg("xzr", 31, dst.bits)
        lhs, rhs = zero, zero
        condition ^= 1                  # see the docstring
        op = 0 if mnemonic == "cset" else 1
        o2 = 0 if mnemonic == "cset" else 0
        opcode2 = 0b01 if mnemonic == "cset" else 0b00
        _word(out, (dst.sf << 31) | (op << 30) | (0b011010100 << 21)
              | (rhs.num << 16) | (condition << 12) | (opcode2 << 10)
              | (lhs.num << 5) | dst.num)
        return
    dst, lhs, rhs = ops[0], ops[1], ops[2]
    assert isinstance(dst, Reg) and isinstance(lhs, Reg) and isinstance(rhs, Reg)
    op, opcode2 = {"csel": (0, 0b00), "csinc": (0, 0b01),
                   "csinv": (1, 0b00), "cneg": (1, 0b01)}[mnemonic]
    _word(out, (dst.sf << 31) | (op << 30) | (0b011010100 << 21)
          | (rhs.num << 16) | (condition << 12) | (opcode2 << 10)
          | (lhs.num << 5) | dst.num)


def _loadstore(mnemonic: str, ops: list[Operand], out: Encoded) -> None:
    reg, where = ops[0], ops[1]
    assert isinstance(reg, Reg) and isinstance(where, Mem)
    entry = _LDST[mnemonic]
    if entry is None:
        # `ldr` and `str` take their width from the REGISTER, which is the
        # only place it is written: `ldr w0` is four bytes and `ldr x0` eight.
        size = {8: 0b00, 16: 0b01, 32: 0b10, 64: 0b11, 128: 0b00}[reg.bits]
        vector = 1 if reg.kind == "fp" else 0
        opc = (0b01 if mnemonic == "ldr" else 0b00)
        if reg.kind == "fp" and reg.bits == 128:
            opc |= 0b10
        access = reg.bits
    else:
        size, vector, opc, access = entry
        if opc is None:
            opc = 0b10 if reg.bits == 64 else 0b11
    scale = access // 8

    if where.index != "none":
        raise EncodeError(
            f"{mnemonic} with a writeback index; only ldp and stp use one")
    offset = where.offset
    if offset % scale or offset < 0:
        # THE UNSCALED FORM, which takes a signed nine-bit byte offset. A
        # negative or unaligned offset has no unsigned-scaled encoding at all.
        if not _fits_signed(offset, 9):
            raise EncodeError(f"{mnemonic} offset {offset} is not encodable")
        _word(out, (size << 30) | (0b111 << 27) | (vector << 26)
              | (opc << 22) | ((offset & 0x1FF) << 12)
              | (where.base.num << 5) | reg.num)
        return
    scaled = offset // scale
    if not _fits_unsigned(scaled, 12):
        raise EncodeError(f"{mnemonic} offset {offset} does not fit twelve bits")
    _word(out, (size << 30) | (0b111 << 27) | (vector << 26) | (0b01 << 24)
          | (opc << 22) | (scaled << 10) | (where.base.num << 5) | reg.num)


def _pair(mnemonic: str, ops: list[Operand], out: Encoded) -> None:
    """`stp`/`ldp`, which is how the frame record is saved and restored."""
    first, second, where = ops
    assert isinstance(first, Reg) and isinstance(second, Reg)
    assert isinstance(where, Mem)
    load = 1 if mnemonic == "ldp" else 0
    vector = 1 if first.kind == "fp" else 0
    opc = {32: 0b00, 64: 0b10, 128: 0b10}[first.bits]
    if vector:
        opc = {32: 0b00, 64: 0b01, 128: 0b10}[first.bits]
    scale = first.bits // 8
    if where.offset % scale:
        raise EncodeError(f"{mnemonic} offset {where.offset} is not aligned")
    imm7 = where.offset // scale
    if not _fits_signed(imm7, 7):
        raise EncodeError(f"{mnemonic} offset {where.offset} does not fit")
    mode = {"post": 0b001, "pre": 0b011, "none": 0b010}[where.index]
    _word(out, (opc << 30) | (0b101 << 27) | (vector << 26) | (mode << 23)
          | (load << 22) | ((imm7 & 0x7F) << 15) | (second.num << 10)
          | (where.base.num << 5) | first.num)


def _float(mnemonic: str, ops: list[Operand], out: Encoded) -> bool:
    """The floating-point subset. False when this is not one of them."""
    if mnemonic in _FP2:
        dst, lhs, rhs = ops
        assert isinstance(dst, Reg) and isinstance(lhs, Reg)
        assert isinstance(rhs, Reg)
        _word(out, (0b11110 << 24) | (_FTYPE[dst.bits] << 22) | (1 << 21)
              | (rhs.num << 16) | (_FP2[mnemonic] << 12) | (0b10 << 10)
              | (lhs.num << 5) | dst.num)
        return True
    if mnemonic in _FP1 and len(ops) == 2 and \
            all(isinstance(o, Reg) and o.kind == "fp" for o in ops):
        dst, src = ops
        assert isinstance(dst, Reg) and isinstance(src, Reg)
        _word(out, (0b11110 << 24) | (_FTYPE[dst.bits] << 22) | (1 << 21)
              | (_FP1[mnemonic] << 15) | (0b10000 << 10) | (src.num << 5)
              | dst.num)
        return True
    if mnemonic == "fmov":
        # BETWEEN A GENERAL REGISTER AND A FLOATING-POINT ONE, which is how a
        # float reaches the arithmetic above at all. The direction is the
        # `rmode:opcode` field, and the widths must agree.
        dst, src = ops
        assert isinstance(dst, Reg) and isinstance(src, Reg)
        sf = 1 if max(dst.bits, src.bits) == 64 else 0
        ftype = _FTYPE[64 if sf else 32]
        opcode = 0b111 if dst.kind == "fp" else 0b110
        _word(out, (sf << 31) | (0b11110 << 24) | (ftype << 22) | (1 << 21)
              | (opcode << 16) | (src.num << 5) | dst.num)
        return True
    if mnemonic in ("fcmp", "fcmpe"):
        lhs, rhs = ops
        assert isinstance(lhs, Reg)
        # THE LOW FIVE BITS SAY WHICH COMPARISON THIS IS: bit 4 selects the
        # signalling form and bit 3 selects the compare against zero, so a
        # register comparison is all zeroes. Setting bit 4 for `fcmp` made
        # every float comparison the signalling one.
        exception = 0b10000 if mnemonic == "fcmpe" else 0
        if isinstance(rhs, Imm):
            if rhs.value != 0:
                raise EncodeError("fcmp takes only #0.0 as an immediate")
            _word(out, (0b11110 << 24) | (_FTYPE[lhs.bits] << 22) | (1 << 21)
                  | (0b1000 << 10) | (lhs.num << 5) | exception | 0b01000)
            return True
        assert isinstance(rhs, Reg)
        _word(out, (0b11110 << 24) | (_FTYPE[lhs.bits] << 22) | (1 << 21)
              | (rhs.num << 16) | (0b1000 << 10) | (lhs.num << 5) | exception)
        return True
    if mnemonic in ("fcvtzs", "fcvtzu", "scvtf", "ucvtf"):
        dst, src = ops
        assert isinstance(dst, Reg) and isinstance(src, Reg)
        general = dst if dst.kind == "gpr" else src
        floating = src if dst.kind == "gpr" else dst
        rmode, opcode = {"fcvtzs": (0b11, 0b000), "fcvtzu": (0b11, 0b001),
                         "scvtf": (0b00, 0b010), "ucvtf": (0b00, 0b011)}[mnemonic]
        _word(out, (general.sf << 31) | (0b11110 << 24)
              | (_FTYPE[floating.bits] << 22) | (1 << 21) | (rmode << 19)
              | (opcode << 16) | (src.num << 5) | dst.num)
        return True
    if mnemonic == "fcvt":
        dst, src = ops
        assert isinstance(dst, Reg) and isinstance(src, Reg)
        _word(out, (0b11110 << 24) | (_FTYPE[src.bits] << 22) | (1 << 21)
              | (0b0001 << 17) | (_FTYPE[dst.bits] << 15) | (0b10000 << 10)
              | (src.num << 5) | dst.num)
        return True
    return False


def resolve(out: Encoded) -> None:
    """Patch every branch to a label in this function.

    ONE PASS IS ENOUGH, and for a better reason than on x86: every AArch64
    instruction is four bytes whatever it branches to, so no patch can change
    a distance and nothing needs re-laying-out.
    """
    for fix in out.fixups:
        if fix.label not in out.labels:
            raise EncodeError(f"branch to unknown label {fix.label!r}")
        words = (out.labels[fix.label] - fix.at) // 4
        current = struct.unpack("<I", bytes(out.code[fix.at:fix.at + 4]))[0]
        if fix.width == "b26":
            if not _fits_signed(words, 26):
                raise EncodeError(f"branch to {fix.label!r} is too far")
            patched = current | (words & 0x03FFFFFF)
        else:
            if not _fits_signed(words, 19):
                raise EncodeError(f"branch to {fix.label!r} is too far")
            patched = current | ((words & 0x7FFFF) << 5)
        out.code[fix.at:fix.at + 4] = struct.pack("<I", patched)


def encode_function(lines: list[str]) -> Encoded:
    """Every line of one function, encoded, with its branches resolved."""
    out = Encoded()
    for line in lines:
        encode_line(line, out)
    resolve(out)
    return out
