"""
AArch64 (ARM64) code generator.

Translates one IRFunc to a flat byte stream using the register allocation
produced by regalloc.py. Ported from _backends/x86_64/codegen.py, covering
the complete IR op vocabulary asmpython/_compiler/ir_lower.py actually
emits (39 ops -- confirmed by grep against ir_lower.py's own IRInstr call
sites). SIMD packed ops, typed byte/word/dword memory access, and TLS are
deliberately NOT ported: ir_lower.py never emits them (they only exist on
the x86-64 side for stdlib runtime shims written directly against the IR,
which have no AArch64 equivalent yet either) -- adding dead surface here
would just be unverifiable guesswork.

AArch64's 3-operand instruction encoding removes an entire class of
hazards the x86-64 module has to work around: no destination-must-equal-
an-operand constraint (so no "compute into a_r and hope it's safe to
mutate" logic), no RAX:RDX-pinned division (SDIV/UDIV write to any GP
register directly), no RCX/CL-pinned variable shift (LSL/LSR/ASR take the
count as an ordinary register operand). The `_gp`/`_dst_gp_spillable`
scratch-collision-avoidance pattern (`alt_scratch`) is still needed and
ported as-is -- that hazard (two simultaneously-spilled operands sharing
one scratch register) is about the allocator's spill strategy, not the
ISA, and applies identically here.

Branch fixups: unlike x86-64's variable-length encoding (where a disp32
field can be spliced into 4 fixed bytes independent of everything else in
the instruction), AArch64's branch offset lives in specific bit ranges of
the SAME 4-byte instruction word as the opcode -- patching a branch target
means re-encoding the whole word, not just the offset bytes. `_patch_branch`
re-derives the instruction kind from its already-emitted opcode bits.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from .encoder import (
    Reg, VReg, Cond,
    ARG_REGS, FP_ARG_REGS,
    add_reg, sub_reg, and_reg, orr_reg, eor_reg, mul, sdiv, udiv,
    mov_reg, neg_reg, add_imm, sub_imm, cmp_reg, cmp_imm,
    movz, movk, mov_imm64,
    ldr_imm, str_imm, ldr_imm_d, str_imm_d, ldp, stp,
    b, bl, blr, ret, b_cond, cbz, cbnz, adrp, adr,
    csel, cset, nop, svc,
    fadd, fsub, fmul, fdiv, fneg, fabs_, fsqrt,
    fmov_reg, fmov_from_gp, fmov_to_gp, fcmp,
    scvtf, fcvtzs,
    add_imm_lsl12, sub_imm_lsl12,
)
from .regalloc import AllocResult, RegLoc, VLoc, StackLoc, Location

# ELF relocation types for AArch64 (mirrors x86_64/codegen.py's own
# R_X86_64_* constants -- defined here, consumed by elf.py, to avoid a
# circular import). Values from the ELF for the ARM 64-bit Architecture
# spec (ARM IHI 0056).
R_AARCH64_CALL26           = 283  # BL/B to an external function symbol
R_AARCH64_ADR_PREL_PG_HI21 = 275  # ADRP's 21-bit page-relative immediate
R_AARCH64_ADD_ABS_LO12_NC  = 277  # ADD's 12-bit low-page-offset immediate


@dataclass
class FuncCode:
    name:       str
    code:       bytes
    # (offset_of_field, symbol_name, reloc_type) — external relocations
    relocs:     list[tuple[int, str, int]] = field(default_factory=list)
    visibility: str | None = None
    debug_locs: list[tuple[int, str, int]] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_float(type_name: str) -> bool:
    return type_name in ("f32", "f64")


class FuncCodegen:
    # Scratch registers, reserved -- must not appear in regalloc.py's pools.
    _SCRATCH   = Reg.X13
    _SCRATCH2  = Reg.X14
    _SCRATCH3  = Reg.X15   # dedicated address/third-operand scratch
    _SCRATCH_FP  = VReg.V14
    _SCRATCH_FP2 = VReg.V15

    def __init__(self, func: Any, alloc: AllocResult, abi: str = "aapcs64") -> None:
        self.func  = func
        self.alloc = alloc
        self.abi   = abi
        self.buf   = bytearray()
        self.block_off: dict[str, int] = {}
        # (patch_offset, label_or_symbol, reloc_type, kind) — kind tells
        # _patch_branch how to re-encode; reloc_type=0 means internal
        # branch (resolved in this same pass, never becomes an ELF reloc).
        self.fixups: list[tuple[int, str, int, str]] = []
        self._debug_locs: list[tuple[int, str, int]] = []

    # ── Emit helpers ──────────────────────────────────────────────────────────

    def _emit(self, b_: bytes) -> None:
        self.buf.extend(b_)

    def _pos(self) -> int:
        return len(self.buf)

    def _emit_word(self, word_bytes: bytes) -> int:
        """Emit a 4-byte instruction, return its own start offset."""
        off = self._pos()
        self._emit(word_bytes)
        return off

    # ── Location helpers ──────────────────────────────────────────────────────

    def _loc(self, val: Any) -> Location:
        return self.alloc.locs[val.name]

    def _gp(self, val: Any, alt_scratch: bool = False) -> tuple[Reg, bytes]:
        """Get a GP register holding val. Emits a load from stack if spilled."""
        scratch = self._SCRATCH2 if alt_scratch else self._SCRATCH
        slot = self.alloc.alloca_slots.get(val.name)
        if slot is not None:
            return scratch, self._addr_fp(scratch, slot)
        loc = self._loc(val)
        if isinstance(loc, RegLoc):
            return loc.reg, b""
        return scratch, self._load_stack_gp(scratch, loc.offset)

    def _fp(self, val: Any, alt_scratch: bool = False) -> tuple[VReg, bytes]:
        """Get a D-register holding val (f64). Emits load if spilled."""
        loc = self._loc(val)
        if isinstance(loc, VLoc):
            return loc.reg, b""
        scratch = self._SCRATCH_FP2 if alt_scratch else self._SCRATCH_FP
        return scratch, self._load_stack_fp(scratch, loc.offset)

    def _addr_fp(self, dst: Reg, offset: int) -> bytes:
        """Materialize X29 + offset into dst, for either sign and large frames."""
        if offset >= 0:
            return self._imm_add_or_sub(dst, Reg.X29, offset, negate=False)
        return self._imm_add_or_sub(dst, Reg.X29, -offset, negate=True)

    def _imm_add_or_sub(self, dst: Reg, src: Reg, mag: int, *, negate: bool) -> bytes:
        """dst = src +/- mag, handling the imm12 and LSL#12 pieces."""
        lo = mag & 0xFFF
        hi = (mag >> 12) & 0xFFF
        assert mag < (1 << 24), f"offset {mag} exceeds this backend's 24-bit addressing range"
        out = b""
        cur = src
        if hi:
            fn = sub_imm_lsl12 if negate else add_imm_lsl12
            out += fn(dst, cur, hi)
            cur = dst
        if lo or not hi:
            fn = sub_imm if negate else add_imm
            out += fn(dst, cur, lo)
        elif cur != dst:
            out += mov_reg(dst, cur)
        return out

    def _load_stack_gp(self, dst: Reg, offset: int) -> bytes:
        """Load an X register from an FP-relative StackLoc.

        The encoder's LDR immediate form is unsigned, while regalloc deliberately
        uses negative offsets below X29. Materialize the address through reserved
        X15, then issue an offset-zero load. Positive incoming-argument offsets
        can use the direct scaled form.
        """
        if 0 <= offset <= 32760 and offset % 8 == 0:
            return ldr_imm(dst, Reg.X29, offset)
        assert dst != self._SCRATCH3
        return self._addr_fp(self._SCRATCH3, offset) + ldr_imm(dst, self._SCRATCH3, 0)

    def _store_stack_gp(self, src: Reg, offset: int) -> bytes:
        if 0 <= offset <= 32760 and offset % 8 == 0:
            return str_imm(src, Reg.X29, offset)
        assert src != self._SCRATCH3
        return self._addr_fp(self._SCRATCH3, offset) + str_imm(src, self._SCRATCH3, 0)

    def _load_stack_fp(self, dst: VReg, offset: int) -> bytes:
        if 0 <= offset <= 32760 and offset % 8 == 0:
            return ldr_imm_d(dst, Reg.X29, offset)
        return self._addr_fp(self._SCRATCH3, offset) + ldr_imm_d(dst, self._SCRATCH3, 0)

    def _store_stack_fp(self, src: VReg, offset: int) -> bytes:
        if 0 <= offset <= 32760 and offset % 8 == 0:
            return str_imm_d(src, Reg.X29, offset)
        return self._addr_fp(self._SCRATCH3, offset) + str_imm_d(src, self._SCRATCH3, 0)

    def _dst_gp(self, result: Any) -> Reg:
        loc = self._loc(result)
        assert isinstance(loc, RegLoc), f"GP result expected for {result.name}"
        return loc.reg

    def _dst_fp(self, result: Any) -> VReg:
        loc = self._loc(result)
        assert isinstance(loc, VLoc), f"FP result expected for {result.name}"
        return loc.reg

    def _dst_gp_spillable(self, result: Any, alt_scratch: bool = False) -> "tuple[Reg, Callable[[], None]]":
        loc = self._loc(result)
        if isinstance(loc, RegLoc):
            return loc.reg, lambda: None
        assert isinstance(loc, StackLoc), f"GP result expected for {result.name}"
        scratch = self._SCRATCH2 if alt_scratch else self._SCRATCH
        return scratch, lambda: self._emit(self._store_stack_gp(scratch, loc.offset))

    def _dst_fp_spillable(self, result: Any) -> "tuple[VReg, Callable[[], None]]":
        loc = self._loc(result)
        if isinstance(loc, VLoc):
            return loc.reg, lambda: None
        assert isinstance(loc, StackLoc), f"FP result expected for {result.name}"
        scratch = self._SCRATCH_FP
        return scratch, lambda: self._emit(self._store_stack_fp(scratch, loc.offset))

    # ── Prologue / epilogue ───────────────────────────────────────────────────
    # Conventional AAPCS64 frame:
    #   [X29, #0]  saved FP, [X29, #8] saved LR
    #   negative offsets: regalloc locals/spills, then saved GP/FP registers
    #   positive offsets: caller-provided stack arguments

    def _callee_saved_area(self) -> int:
        n_gp = len(self.alloc.callee_saved)
        n_fp = len(self.alloc.callee_saved_fp)
        return 8 * (((n_gp + 1) // 2) * 2 + ((n_fp + 1) // 2) * 2)

    def _prologue(self) -> None:
        # Never use ORR-based mov_reg with SP: register encoding 31 means XZR
        # in ORR, not SP. ADD-immediate #0 is the architectural SP move.
        self._emit(stp(Reg.X29, Reg.X30, Reg.SP, -16, writeback="pre"))
        self._emit(add_imm(Reg.X29, Reg.SP, 0))

        frame_body = self.alloc.stack_bytes + self._callee_saved_area()
        if frame_body:
            self._emit(self._imm_add_or_sub(Reg.SP, Reg.SP, frame_body, negate=True))

        gp_area = 8 * (((len(self.alloc.callee_saved) + 1) // 2) * 2)
        off = -self.alloc.stack_bytes - 8
        for reg in self.alloc.callee_saved:
            self._emit(self._store_stack_gp(reg, off))
            off -= 8

        off = -self.alloc.stack_bytes - gp_area - 8
        for reg in self.alloc.callee_saved_fp:
            self._emit(self._store_stack_fp(reg, off))
            off -= 8

    def _epilogue(self) -> None:
        gp_area = 8 * (((len(self.alloc.callee_saved) + 1) // 2) * 2)
        off = -self.alloc.stack_bytes - 8
        for reg in self.alloc.callee_saved:
            self._emit(self._load_stack_gp(reg, off))
            off -= 8

        off = -self.alloc.stack_bytes - gp_area - 8
        for reg in self.alloc.callee_saved_fp:
            self._emit(self._load_stack_fp(reg, off))
            off -= 8

        self._emit(add_imm(Reg.SP, Reg.X29, 0))
        self._emit(ldp(Reg.X29, Reg.X30, Reg.SP, 16, writeback="post"))
        self._emit(ret())

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _binop_gp(self, result: Any, a: Any, b_val: Any, rrr_fn) -> None:
        a_r, a_ld = self._gp(a)
        b_r, b_ld = self._gp(b_val, alt_scratch=True)
        self._emit(a_ld + b_ld)
        dst, spill = self._dst_gp_spillable(result, alt_scratch=(a_r == self._SCRATCH2 or b_r == self._SCRATCH2))
        self._emit(rrr_fn(dst, a_r, b_r))
        spill()

    def _binop_fp(self, result: Any, a: Any, b_val: Any, rrr_fn) -> None:
        a_x, a_ld = self._fp(a)
        b_x, b_ld = self._fp(b_val, alt_scratch=True)
        self._emit(a_ld + b_ld)
        dst, spill = self._dst_fp_spillable(result)
        self._emit(rrr_fn(dst, a_x, b_x))
        spill()

    def _cmp_set(self, result: Any, a: Any, b_val: Any, cond: Cond) -> None:
        a_r, a_ld = self._gp(a)
        b_r, b_ld = self._gp(b_val, alt_scratch=True)
        self._emit(a_ld + b_ld)
        self._emit(cmp_reg(a_r, b_r))
        dst, spill = self._dst_gp_spillable(result)
        self._emit(cset(dst, cond))
        spill()

    def _fcmp_set(self, result: Any, a: Any, b_val: Any, cond: Cond) -> None:
        a_x, a_ld = self._fp(a)
        b_x, b_ld = self._fp(b_val, alt_scratch=True)
        self._emit(a_ld + b_ld)
        self._emit(fcmp(a_x, b_x))
        dst, spill = self._dst_gp_spillable(result)
        self._emit(cset(dst, cond))
        spill()

    # ── Branch helpers ────────────────────────────────────────────────────────

    def _emit_b(self, label: str) -> None:
        off = self._emit_word(b(0))
        self.fixups.append((off, label, 0, "b"))

    def _emit_b_cond(self, cond: Cond, label: str) -> None:
        off = self._emit_word(b_cond(cond, 0))
        self.fixups.append((off, label, 0, f"b.{cond.name}"))

    def _emit_cbz(self, rt: Reg, label: str) -> None:
        off = self._emit_word(cbz(rt, 0))
        self.fixups.append((off, label, 0, f"cbz:{int(rt)}"))

    def _patch_branch(self, patch_off: int, kind: str, offset_words: int) -> bytes:
        if kind == "b":
            return b(offset_words)
        if kind.startswith("b."):
            return b_cond(Cond[kind[2:]], offset_words)
        if kind.startswith("cbz:"):
            return cbz(Reg(int(kind.split(":", 1)[1])), offset_words)
        if kind == "bl":
            return bl(offset_words)
        raise ValueError(f"unknown branch fixup kind {kind!r}")

    # ── Call ──────────────────────────────────────────────────────────────────

    def _call(self, instr: Any) -> None:
        target_op = instr.operands[0]
        arg_vals  = instr.operands[1:]
        is_indirect = hasattr(target_op, "name") and hasattr(target_op, "type")

        gp_reg_args: list[tuple[Reg, Any, int]] = []
        fp_reg_args: list[tuple[VReg, Any, int]] = []
        stack_args:  list[tuple[Any, int]] = []

        int_i = fp_i = 0
        temp_slots = 0
        for av in arg_vals:
            typ = av.type.name if hasattr(av, "type") else "i64"
            if _is_float(typ):
                if fp_i < len(FP_ARG_REGS):
                    fp_reg_args.append((FP_ARG_REGS[fp_i], av, temp_slots))
                    fp_i += 1; temp_slots += 1
                else:
                    stack_args.append((av, len(stack_args)))
            else:
                if int_i < len(ARG_REGS):
                    gp_reg_args.append((ARG_REGS[int_i], av, temp_slots))
                    int_i += 1; temp_slots += 1
                else:
                    stack_args.append((av, len(stack_args)))

        target_temp_slot: int | None = None
        if is_indirect:
            target_temp_slot = temp_slots
            temp_slots += 1

        stack_arg_bytes = 8 * len(stack_args)
        temp_bytes = 8 * temp_slots
        call_frame = stack_arg_bytes + temp_bytes
        if call_frame % 16:
            call_frame += 16 - (call_frame % 16)

        if call_frame:
            self._emit(self._imm_add_or_sub(Reg.SP, Reg.SP, call_frame, negate=True))

        stack_base = 0
        temp_base = stack_arg_bytes

        for av, stack_i in stack_args:
            off = stack_base + stack_i * 8
            typ = av.type.name if hasattr(av, "type") else "i64"
            if _is_float(typ):
                src_x, ld = self._fp(av)
                self._emit(ld); self._emit(str_imm_d(src_x, Reg.SP, off))
            else:
                src_r, ld = self._gp(av)
                self._emit(ld); self._emit(str_imm(src_r, Reg.SP, off))

        for _dst_r, av, temp_i in gp_reg_args:
            src_r, ld = self._gp(av)
            self._emit(ld); self._emit(str_imm(src_r, Reg.SP, temp_base + 8 * temp_i))
        for _dst_x, av, temp_i in fp_reg_args:
            src_x, ld = self._fp(av)
            self._emit(ld); self._emit(str_imm_d(src_x, Reg.SP, temp_base + 8 * temp_i))
        if target_temp_slot is not None:
            ptr_r, ld = self._gp(target_op)
            self._emit(ld); self._emit(str_imm(ptr_r, Reg.SP, temp_base + 8 * target_temp_slot))

        for dst_r, _av, temp_i in gp_reg_args:
            self._emit(ldr_imm(dst_r, Reg.SP, temp_base + 8 * temp_i))
        for dst_x, _av, temp_i in fp_reg_args:
            self._emit(ldr_imm_d(dst_x, Reg.SP, temp_base + 8 * temp_i))

        if is_indirect:
            self._emit(ldr_imm(self._SCRATCH, Reg.SP, temp_base + 8 * target_temp_slot))
            self._emit(blr(self._SCRATCH))
        else:
            off = self._emit_word(bl(0))
            self.fixups.append((off, str(target_op), R_AARCH64_CALL26, "bl"))

        if call_frame:
            self._emit(self._imm_add_or_sub(Reg.SP, Reg.SP, call_frame, negate=False))

        if instr.result is not None:
            rtyp = instr.result.type.name
            if _is_float(rtyp):
                loc = self._loc(instr.result)
                if isinstance(loc, VLoc):
                    if loc.reg != VReg.V0:
                        self._emit(fmov_reg(loc.reg, VReg.V0))
                else:
                    self._emit(self._store_stack_fp(VReg.V0, loc.offset))
            else:
                loc = self._loc(instr.result)
                if isinstance(loc, RegLoc):
                    if loc.reg != Reg.X0:
                        self._emit(mov_reg(loc.reg, Reg.X0))
                else:
                    self._emit(self._store_stack_gp(Reg.X0, loc.offset))

    # ── Per-instruction dispatch ──────────────────────────────────────────────

    _IOPS = {
        "iadd": add_reg, "isub": sub_reg,
        "imul": lambda d, a, b_: mul(d, a, b_),
        "iand": and_reg, "ior": orr_reg, "ixor": eor_reg,
    }
    _ICMP: dict[str, Cond] = {
        "icmp.eq": Cond.EQ, "icmp.ne": Cond.NE,
        "icmp.lt": Cond.LT, "icmp.le": Cond.LE,
        "icmp.gt": Cond.GT, "icmp.ge": Cond.GE,
        "icmp.ult": Cond.CC, "icmp.ule": Cond.LS,
        "icmp.ugt": Cond.HI, "icmp.uge": Cond.CS,
    }
    _FCMP: dict[str, Cond] = {
        "fcmp.eq": Cond.EQ, "fcmp.ne": Cond.NE,
        "fcmp.lt": Cond.MI, "fcmp.le": Cond.LS,
        "fcmp.gt": Cond.GT, "fcmp.ge": Cond.GE,
    }
    _FOPS = {"fadd": fadd, "fsub": fsub, "fmul": fmul, "fdiv": fdiv}

    def _instr(self, instr: Any) -> None:  # noqa: C901
        op  = instr.op
        r   = instr.result
        ops = instr.operands

        if op in self._IOPS:
            self._binop_gp(r, ops[0], ops[1], self._IOPS[op]); return

        if op in ("idiv", "udiv"):
            fn = sdiv if op == "idiv" else udiv
            self._binop_gp(r, ops[0], ops[1], lambda d, a, b_: fn(d, a, b_)); return

        if op in ("irem", "urem"):
            a_r, a_ld = self._gp(ops[0])
            b_r, b_ld = self._gp(ops[1], alt_scratch=True)
            self._emit(a_ld + b_ld)
            div_fn = sdiv if op == "irem" else udiv
            self._emit(div_fn(self._SCRATCH3, a_r, b_r))
            dst, spill = self._dst_gp_spillable(r)
            word = 0x9B008000 | (int(b_r) << 16) | (int(a_r) << 10) | (int(self._SCRATCH3) << 5) | int(dst)
            self._emit(struct.pack("<I", word))
            spill(); return

        if op == "ineg":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=(src_r == self._SCRATCH))
            self._emit(ld); self._emit(neg_reg(dst, src_r)); spill(); return

        if op == "inot":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=(src_r == self._SCRATCH))
            self._emit(ld)
            word = 0xAA200000 | (int(src_r) << 16) | (int(Reg.XZR) << 5) | int(dst)
            self._emit(struct.pack("<I", word))
            spill(); return

        if op in ("shl", "shr", "sar"):
            val_r, val_ld = self._gp(ops[0])
            count = ops[1]
            self._emit(val_ld)
            dst, spill = self._dst_gp_spillable(r, alt_scratch=(val_r == self._SCRATCH))
            if isinstance(count, int):
                n = count & 0x3F
                if op == "shl":
                    immr = (-n) & 0x3F
                    imms = 63 - n
                    word = 0xD3400000 | (immr << 16) | (imms << 10) | (int(val_r) << 5) | int(dst)
                else:
                    immr = n
                    imms = 63
                    base = 0xD3400000 if op == "shr" else 0x93400000
                    word = base | (immr << 16) | (imms << 10) | (int(val_r) << 5) | int(dst)
                self._emit(struct.pack("<I", word))
            else:
                cnt_r, cnt_ld = self._gp(count, alt_scratch=True)
                self._emit(cnt_ld)
                base = {"shl": 0x9AC02000, "shr": 0x9AC02400, "sar": 0x9AC02800}[op]
                word = base | (int(cnt_r) << 16) | (int(val_r) << 5) | int(dst)
                self._emit(struct.pack("<I", word))
            spill(); return

        if op in self._ICMP:
            self._cmp_set(r, ops[0], ops[1], self._ICMP[op]); return

        if op in self._FOPS:
            self._binop_fp(r, ops[0], ops[1], self._FOPS[op]); return

        if op == "fneg":
            src_x, ld = self._fp(ops[0])
            dst, spill = self._dst_fp_spillable(r)
            self._emit(ld); self._emit(fneg(dst, src_x)); spill(); return

        if op in self._FCMP:
            self._fcmp_set(r, ops[0], ops[1], self._FCMP[op]); return

        if op == "const":
            v = ops[0]
            if r is not None and _is_float(r.type.name):
                bits = struct.pack("<d", float(v))
                imm  = int.from_bytes(bits, "little")
                for w in mov_imm64(self._SCRATCH, imm):
                    self._emit(w)
                loc = self._loc(r)
                if isinstance(loc, VLoc):
                    self._emit(fmov_from_gp(loc.reg, self._SCRATCH))
                else:
                    self._emit(fmov_from_gp(self._SCRATCH_FP, self._SCRATCH))
                    self._emit(self._store_stack_fp(self._SCRATCH_FP, loc.offset))
            else:
                loc = self._loc(r)
                for w in mov_imm64(self._SCRATCH, int(v)):
                    self._emit(w)
                if isinstance(loc, RegLoc):
                    if loc.reg != self._SCRATCH:
                        self._emit(mov_reg(loc.reg, self._SCRATCH))
                else:
                    self._emit(self._store_stack_gp(self._SCRATCH, loc.offset))
            return

        if op == "load":
            ptr_r, ld = self._gp(ops[0])
            self._emit(ld)
            tname = r.type.name
            if tname == "f64":
                loc = self._loc(r)
                if isinstance(loc, VLoc):
                    self._emit(ldr_imm_d(loc.reg, ptr_r, 0))
                else:
                    self._emit(ldr_imm_d(self._SCRATCH_FP, ptr_r, 0))
                    self._emit(self._store_stack_fp(self._SCRATCH_FP, loc.offset))
            else:
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(ldr_imm(loc.reg, ptr_r, 0))
                else:
                    self._emit(ldr_imm(self._SCRATCH, ptr_r, 0))
                    self._emit(self._store_stack_gp(self._SCRATCH, loc.offset))
            return

        if op == "store":
            val, ptr = ops[0], ops[1]
            ptr_r, p_ld = self._gp(ptr)
            self._emit(p_ld)
            tname = val.type.name if hasattr(val, "type") else "i64"
            if tname == "f64":
                src_x, v_ld = self._fp(val, alt_scratch=True)
                self._emit(v_ld); self._emit(str_imm_d(src_x, ptr_r, 0))
            else:
                src_r, v_ld = self._gp(val, alt_scratch=True)
                self._emit(v_ld); self._emit(str_imm(src_r, ptr_r, 0))
            return

        if op == "gep":
            ptr_r, p_ld = self._gp(ops[0])
            self._emit(p_ld)
            dst, spill = self._dst_gp_spillable(r, alt_scratch=(ptr_r == self._SCRATCH))
            if isinstance(ops[1], int):
                self._emit(self._imm_add_or_sub(dst, ptr_r, ops[1], negate=False) if ops[1] >= 0
                           else self._imm_add_or_sub(dst, ptr_r, -ops[1], negate=True))
            else:
                idx_r, i_ld = self._gp(ops[1], alt_scratch=True)
                self._emit(i_ld)
                self._emit(add_reg(dst, ptr_r, idx_r))
            spill(); return

        if op == "alloca":
            return

        if op in ("sext", "zext", "trunc"):
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=(src_r == self._SCRATCH))
            self._emit(ld)
            if src_r != dst:
                self._emit(mov_reg(dst, src_r))
            spill(); return

        if op == "sitofp":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_fp_spillable(r)
            self._emit(ld); self._emit(scvtf(dst, src_r)); spill(); return

        if op == "fptosi":
            src_x, ld = self._fp(ops[0])
            dst, spill = self._dst_gp_spillable(r)
            self._emit(ld); self._emit(fcvtzs(dst, src_x)); spill(); return

        if op == "bitcast_i2f":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_fp_spillable(r)
            self._emit(ld); self._emit(fmov_from_gp(dst, src_r)); spill(); return

        if op == "bitcast_f2i":
            src_x, ld = self._fp(ops[0])
            dst, spill = self._dst_gp_spillable(r)
            self._emit(ld); self._emit(fmov_to_gp(dst, src_x)); spill(); return

        if op == "ret":
            if ops:
                val = ops[0]
                if _is_float(val.type.name):
                    src_x, ld = self._fp(val)
                    self._emit(ld)
                    if src_x != VReg.V0:
                        self._emit(fmov_reg(VReg.V0, src_x))
                else:
                    src_r, ld = self._gp(val)
                    self._emit(ld)
                    if src_r != Reg.X0:
                        self._emit(mov_reg(Reg.X0, src_r))
            self._epilogue(); return

        if op == "br":
            self._emit_b(str(ops[0])); return

        if op == "br.t":
            cond_r, ld = self._gp(ops[0])
            self._emit(ld)
            self._emit_cbz(cond_r, str(ops[2]))
            self._emit_b(str(ops[1]))
            return

        if op == "call":
            self._call(instr); return

        if op == "mov":
            if r is None:
                return
            if _is_float(r.type.name):
                dst = self._dst_fp(r) if isinstance(self._loc(r), VLoc) else None
                src_x, ld = self._fp(ops[0])
                self._emit(ld)
                if dst is not None:
                    if src_x != dst:
                        self._emit(fmov_reg(dst, src_x))
                else:
                    dst2, spill = self._dst_fp_spillable(r)
                    if src_x != dst2:
                        self._emit(fmov_reg(dst2, src_x))
                    spill()
            else:
                src_r, ld = self._gp(ops[0])
                dst, spill = self._dst_gp_spillable(r, alt_scratch=(src_r == self._SCRATCH))
                self._emit(ld)
                if src_r != dst:
                    self._emit(mov_reg(dst, src_r))
                spill()
            return

        if op in ("global_addr", "str_global"):
            loc = self._loc(r)
            dst = loc.reg if isinstance(loc, RegLoc) else self._SCRATCH
            adrp_off = self._emit_word(adrp(dst, 0))
            self.fixups.append((adrp_off, str(ops[0]), R_AARCH64_ADR_PREL_PG_HI21, "adrp"))
            add_off = self._emit_word(add_imm(dst, dst, 0))
            self.fixups.append((add_off, str(ops[0]), R_AARCH64_ADD_ABS_LO12_NC, "add_lo12"))
            if isinstance(loc, StackLoc):
                self._emit(self._store_stack_gp(dst, loc.offset))
            return

        if op == "debug_loc":
            fname = str(ops[0]) if ops else "<unknown>"
            line  = int(ops[1]) if len(ops) > 1 else 0
            self._debug_locs.append((self._pos(), fname, line))
            return

        self._emit(nop())

    # ── Main entry ────────────────────────────────────────────────────────────

    def compile(self) -> FuncCode:
        self._prologue()

        for block in self.func.blocks:
            self.block_off[block.label] = self._pos()
            for instr in block.instrs:
                self._instr(instr)

        relocs: list[tuple[int, str, int]] = []
        for patch_off, label, rtype, kind in self.fixups:
            if kind in ("adrp", "add_lo12"):
                relocs.append((patch_off, label, rtype))
                continue
            if label in self.block_off and kind != "bl":
                offset_words = (self.block_off[label] - patch_off) // 4
                new_word = self._patch_branch(patch_off, kind, offset_words)
                self.buf[patch_off:patch_off + 4] = new_word
            else:
                relocs.append((patch_off, label, rtype))

        return FuncCode(
            name=self.func.name,
            code=bytes(self.buf),
            relocs=relocs,
            visibility=getattr(self.func, "visibility", None),
            debug_locs=self._debug_locs,
        )


def compile_func(func: Any, alloc: AllocResult, abi: str = "aapcs64") -> FuncCode:
    return FuncCodegen(func, alloc, abi).compile()
