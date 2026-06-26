"""
IR → ternary machine code.

Calling convention
------------------
  r0  = arg 0 / return value
  r1  = arg 1
  r2  = arg 2
  r3  = arg 3
  r4-r14 = temporaries; recycled after last IR use (via liveness pre-scan)
  r15 = never allocated (scratch reserved for future multi-step sequences)

Local variables (alloca slots) live at pre-assigned static memory
addresses (resolved by the module-level allocator in __init__.py).
All locals survive calls because they are in memory, not registers.

SSA temps (r4-r14) are NOT saved across calls. Programs where an
expression temp lives across a function call will produce wrong
results. Programs lowered by ir_lower.py avoid this because all
locals are in alloca slots (memory) before calling.
"""

from __future__ import annotations

from . import encoder as E
from ..._compiler.ir import IRFunc, IRBlock, IRInstr, IRValue

_PARAM_REGS   = [0, 1, 2, 3]
_FIRST_TEMP   = 4
_SCRATCH      = 15
_LAST_TEMP    = _SCRATCH - 1   # r4 .. r14 are the recyclable temp pool


class _RegPool:
    """Simple stack-based register allocator with explicit free()."""

    def __init__(self) -> None:
        # r14 down to r4 — allocate low registers first (slightly nicer)
        self._avail: list[int] = list(range(_LAST_TEMP, _FIRST_TEMP - 1, -1))

    def alloc(self, hint: str = "") -> int:
        if not self._avail:
            raise OverflowError(
                f"Too many live SSA values — ran out of temp registers"
                + (f" (allocating for {hint})" if hint else "")
            )
        return self._avail.pop()

    def free(self, r: int) -> None:
        if _FIRST_TEMP <= r <= _LAST_TEMP:
            self._avail.append(r)


def _compute_last_use(func: IRFunc) -> dict[str, tuple[int, int]]:
    """
    Returns {irvalue_name: (block_idx, instr_idx)} for the last instruction
    in the function that references each IRValue as an operand.
    """
    last: dict[str, tuple[int, int]] = {}
    for b_idx, block in enumerate(func.blocks):
        for i_idx, instr in enumerate(block.instrs):
            for op in instr.operands:
                if isinstance(op, IRValue):
                    last[op.name] = (b_idx, i_idx)
    return last


class TernaryFuncCodegen:
    """Translates one IRFunc to a list of memory-cell integers."""

    def __init__(
        self,
        func: IRFunc,
        func_addr: int,
        frame_addr: int,
        global_data: dict[str, object],
    ) -> None:
        self.func = func
        self.func_addr = func_addr
        self.frame_addr = frame_addr
        self.global_data = global_data

        self.words: list[int] = []
        self.label_map: dict[str, int] = {}
        self.fixups: list[tuple[int, int, str]] = []

        self._pool = _RegPool()
        self._reg: dict[str, int] = {}
        self._alloca: dict[str, int] = {}
        self._global_ref: dict[str, str] = {}
        self._last_use: dict[str, tuple[int, int]] = {}

        self._next_slot = 0
        self._cur_blk: int = 0
        self._cur_ins: int = 0

        # Parameter registers are fixed; NOT in the pool.
        for i, p in enumerate(func.params):
            if i < len(_PARAM_REGS):
                self._reg[p.name] = _PARAM_REGS[i]

    # ── Register allocation ───────────────────────────────────────────────────

    def _reg_of(self, v: IRValue) -> int:
        if v.name not in self._reg:
            self._reg[v.name] = self._pool.alloc(v.name)
        return self._reg[v.name]

    def _free_if_dead(self, v: IRValue) -> None:
        """Free v's register if this instruction is its last use."""
        lu = self._last_use.get(v.name)
        if lu == (self._cur_blk, self._cur_ins):
            r = self._reg.get(v.name)
            if r is not None:
                self._pool.free(r)

    def _free_operand_registers(self, instr: IRInstr) -> None:
        for op in instr.operands:
            if isinstance(op, IRValue):
                self._free_if_dead(op)

    # ── Frame allocation ──────────────────────────────────────────────────────

    def _alloc_slot(self, ptr_name: str) -> int:
        addr = self.frame_addr + self._next_slot
        self._alloca[ptr_name] = addr
        self._next_slot += 1
        return addr

    def slots_used(self) -> int:
        return self._next_slot

    # ── Emission helpers ──────────────────────────────────────────────────────

    def _emit(self, ws: list[int]) -> None:
        self.words.extend(ws)

    def _cur_abs(self) -> int:
        return self.func_addr + len(self.words)

    def _emit_fixup(self, emit_fn, label: str) -> None:
        self._emit(emit_fn(0))
        # New format: header + 1 operand word; operand is the last word emitted.
        op_idx = len(self.words) - 1
        self.fixups.append((op_idx, label))

    def _emit_jmp(self, label: str):  self._emit_fixup(E.jmp, label)
    def _emit_jnz(self, label: str):  self._emit_fixup(E.jnz, label)
    def _emit_jz (self, label: str):  self._emit_fixup(E.jz,  label)
    def _emit_jl (self, label: str):  self._emit_fixup(E.jl,  label)
    def _emit_jg (self, label: str):  self._emit_fixup(E.jg,  label)
    def _emit_jle(self, label: str):  self._emit_fixup(E.jle, label)
    def _emit_jge(self, label: str):  self._emit_fixup(E.jge, label)
    def _emit_call(self, label: str): self._emit_fixup(E.call_abs, label)

    # ── IR instruction lowering ───────────────────────────────────────────────

    def _lower_alloca(self, instr: IRInstr) -> None:
        assert instr.result is not None
        if instr.result.name not in self._alloca:
            self._alloc_slot(instr.result.name)

    def _lower_load(self, instr: IRInstr) -> None:
        ptr = instr.operands[0]
        assert isinstance(ptr, IRValue) and instr.result is not None
        dst = self._reg_of(instr.result)
        if ptr.name in self._alloca:
            self._emit(E.load_i(self._alloca[ptr.name], dst))
        else:
            self._emit(E.load_r(self._reg_of(ptr), dst))

    def _lower_store(self, instr: IRInstr) -> None:
        val, ptr = instr.operands[0], instr.operands[1]
        assert isinstance(val, IRValue) and isinstance(ptr, IRValue)
        r_val = self._reg_of(val)
        if ptr.name in self._alloca:
            self._emit(E.store_i(self._alloca[ptr.name], r_val))
        else:
            self._emit(E.store_r(self._reg_of(ptr), r_val))

    def _lower_const(self, instr: IRInstr) -> None:
        assert instr.result is not None
        self._emit(E.movi(self._reg_of(instr.result), int(instr.operands[0])))

    def _lower_global_addr(self, instr: IRInstr) -> None:
        assert instr.result is not None
        name = instr.operands[0]
        assert isinstance(name, str)
        self._global_ref[instr.result.name] = name
        self._reg_of(instr.result)   # reserve a register; no code emitted

    def _lower_binop(self, op: str, instr: IRInstr) -> None:
        a, b = instr.operands[0], instr.operands[1]
        assert isinstance(a, IRValue) and isinstance(b, IRValue)
        assert instr.result is not None
        r_a   = self._reg_of(a)
        r_b   = self._reg_of(b)
        r_res = self._reg_of(instr.result)
        # result = a OP b  →  mov r_res, r_a ; OP r_b, r_res
        self._emit(E.mov(r_res, r_a))
        {
            "iadd": lambda: self._emit(E.add (r_b, r_res)),
            "isub": lambda: self._emit(E.sub (r_b, r_res)),
            "imul": lambda: self._emit(E.mul (r_b, r_res)),
            "idiv": lambda: self._emit(E.div_(r_b, r_res)),
            "irem": lambda: self._emit(E.mod_(r_b, r_res)),
            "iand": lambda: self._emit(E.iand(r_b, r_res)),
            "ior":  lambda: self._emit(E.ior (r_b, r_res)),
            "ixor": lambda: self._emit(E.ixor(r_b, r_res)),
            "shl":  lambda: self._emit(E.shl (r_b, r_res)),
            "shr":  lambda: self._emit(E.shr (r_b, r_res)),
        }[op]()

    def _lower_icmp(self, op: str, instr: IRInstr) -> None:
        """
        Materialize a comparison result as 0 or 1.

        CMP(a, b) sets FLAGS = b - a, so:
          icmp.lt [a,b]: a<b → b-a>0 → JG  (not NEG, not ZERO)
          icmp.le [a,b]: a≤b → b-a≥0 → JGE (not NEG)
          icmp.gt [a,b]: a>b → b-a<0 → JL  (NEG)
          icmp.ge [a,b]: a≥b → b-a≤0 → JLE (NEG or ZERO)
          icmp.eq [a,b]: a=b → b-a=0 → JZ  (ZERO)
          icmp.ne [a,b]: a≠b → b-a≠0 → JNZ (not ZERO)
        """
        a, b = instr.operands[0], instr.operands[1]
        assert isinstance(a, IRValue) and isinstance(b, IRValue)
        assert instr.result is not None
        r_a   = self._reg_of(a)
        r_b   = self._reg_of(b)
        r_res = self._reg_of(instr.result)

        seq = self._cur_abs()
        lbl_taken = f"__icmp_t_{seq}"
        lbl_done  = f"__icmp_d_{seq}"

        self._emit(E.movi(r_res, 0))
        self._emit(E.cmp_rr(r_a, r_b))
        {
            "icmp.lt": self._emit_jg,
            "icmp.le": self._emit_jge,
            "icmp.gt": self._emit_jl,
            "icmp.ge": self._emit_jle,
            "icmp.eq": self._emit_jz,
            "icmp.ne": self._emit_jnz,
        }[op](lbl_taken)
        self._emit_jmp(lbl_done)

        self.label_map[lbl_taken] = self._cur_abs()
        self._emit(E.movi(r_res, 1))
        self.label_map[lbl_done] = self._cur_abs()

    def _lower_br(self, instr: IRInstr) -> None:
        label = instr.operands[0]
        assert isinstance(label, str)
        self._emit_jmp(label)

    def _lower_br_t(self, instr: IRInstr) -> None:
        cond, true_lbl, false_lbl = instr.operands
        assert isinstance(cond, IRValue)
        assert isinstance(true_lbl, str) and isinstance(false_lbl, str)
        r_cond = self._reg_of(cond)
        self._emit(E.cmp_ir(0, r_cond))   # FLAGS = r_cond
        self._emit_jnz(true_lbl)
        self._emit_jmp(false_lbl)

    def _lower_ret(self, instr: IRInstr) -> None:
        if instr.operands:
            val = instr.operands[0]
            assert isinstance(val, IRValue)
            r_val = self._reg_of(val)
            if r_val != 0:
                self._emit(E.mov(0, r_val))
        self._emit(E.ret_())

    def _lower_call(self, instr: IRInstr) -> None:
        func_name = instr.operands[0]
        args = instr.operands[1:]
        assert isinstance(func_name, str)

        if func_name == "printf":
            self._lower_printf(instr, args)
            return
        if func_name == "mem_load":
            self._lower_mem_load(instr, args)
            return
        if func_name == "mem_store":
            self._lower_mem_store(instr, args)
            return
        if func_name == "diskread":
            self._lower_diskread(instr, args)
            return
        if func_name == "diskwrite":
            self._lower_diskwrite(instr, args)
            return
        if func_name == "call_addr":
            self._lower_call_addr(instr, args)
            return
        if func_name == "print_char":
            self._lower_print_char(instr, args)
            return
        if func_name == "read_char":
            self._lower_read_char(instr, args)
            return

        # Move args into r0-r3. Arg registers are always r4+ (fresh temps from
        # ir_lower.py), so no swap conflict with r0-r3 occurs in practice.
        for i, arg in enumerate(args):
            if i >= len(_PARAM_REGS):
                break
            if isinstance(arg, IRValue):
                r_arg = self._reg_of(arg)
                if r_arg != _PARAM_REGS[i]:
                    self._emit(E.mov(_PARAM_REGS[i], r_arg))

        self._emit_call(func_name)

        if instr.result is not None:
            r_res = self._reg_of(instr.result)
            if r_res != 0:
                self._emit(E.mov(r_res, 0))

    # ── OS intrinsics ─────────────────────────────────────────────────────────

    def _lower_mem_load(self, instr: IRInstr, args: list) -> None:
        """mem_load(addr) -> int  — LOAD r_addr, r_result"""
        assert len(args) >= 1 and isinstance(args[0], IRValue)
        assert instr.result is not None
        r_addr = self._reg_of(args[0])
        r_res  = self._reg_of(instr.result)
        self._emit(E.load_r(r_addr, r_res))

    def _lower_mem_store(self, instr: IRInstr, args: list) -> None:
        """mem_store(addr, val) — STORE r_addr, r_val"""
        assert len(args) >= 2
        assert isinstance(args[0], IRValue) and isinstance(args[1], IRValue)
        r_addr = self._reg_of(args[0])
        r_val  = self._reg_of(args[1])
        self._emit(E.store_r(r_addr, r_val))
        if instr.result is not None:
            r_res = self._reg_of(instr.result)
            self._emit(E.movi(r_res, 0))

    def _lower_diskread(self, instr: IRInstr, args: list) -> None:
        """diskread(ram_addr, sector) — DISKREAD r_dst, r_sector"""
        assert len(args) >= 2
        assert isinstance(args[0], IRValue) and isinstance(args[1], IRValue)
        r_dst = self._reg_of(args[0])
        r_sec = self._reg_of(args[1])
        self._emit(E.diskread(r_dst, r_sec))
        if instr.result is not None:
            r_res = self._reg_of(instr.result)
            self._emit(E.movi(r_res, 0))

    def _lower_diskwrite(self, instr: IRInstr, args: list) -> None:
        """diskwrite(ram_addr, sector) — DISKWRITE r_src, r_sector"""
        assert len(args) >= 2
        assert isinstance(args[0], IRValue) and isinstance(args[1], IRValue)
        r_src = self._reg_of(args[0])
        r_sec = self._reg_of(args[1])
        self._emit(E.diskwrite(r_src, r_sec))
        if instr.result is not None:
            r_res = self._reg_of(instr.result)
            self._emit(E.movi(r_res, 0))

    def _lower_call_addr(self, instr: IRInstr, args: list) -> None:
        """call_addr(addr) — indirect CALL via register (for launching loaded programs)"""
        assert len(args) >= 1 and isinstance(args[0], IRValue)
        r_addr = self._reg_of(args[0])
        self._emit(E.call_r(r_addr))
        if instr.result is not None:
            r_res = self._reg_of(instr.result)
            if r_res != 0:
                self._emit(E.mov(r_res, 0))

    def _lower_print_char(self, instr: IRInstr, args: list) -> None:
        """print_char(ch) — OUT port 1, r_ch  (character output)"""
        assert len(args) >= 1 and isinstance(args[0], IRValue)
        r_ch = self._reg_of(args[0])
        self._emit(E.out_r(1, r_ch))
        if instr.result is not None:
            self._emit(E.movi(self._reg_of(instr.result), 0))

    def _lower_read_char(self, instr: IRInstr, args: list) -> None:
        """read_char() -> int — IN port 1, r_result  (blocking character input)"""
        assert instr.result is not None
        r_res = self._reg_of(instr.result)
        self._emit(E.in_r(1, r_res))

    def _lower_printf(self, instr: IRInstr, args: list) -> None:
        """Map printf(fmt, val) → OUT #0, r_val (port 0 = integer print)."""
        if len(args) >= 2:
            fmt_ptr = args[0]
            val     = args[1]
            if isinstance(fmt_ptr, IRValue) and isinstance(val, IRValue):
                gname   = self._global_ref.get(fmt_ptr.name, "")
                fmt_str = str(self.global_data.get(gname, ""))
                if "%lld" in fmt_str or "%d" in fmt_str:
                    self._emit(E.out_r(0, self._reg_of(val)))
        if instr.result is not None:
            self._emit(E.movi(self._reg_of(instr.result), 0))

    def _lower_instr(self, instr: IRInstr) -> None:
        op = instr.op
        if   op == "alloca":    self._lower_alloca(instr)
        elif op == "load":      self._lower_load(instr)
        elif op == "store":     self._lower_store(instr)
        elif op == "const":     self._lower_const(instr)
        elif op == "global_addr": self._lower_global_addr(instr)
        elif op in ("iadd","isub","imul","idiv","irem",
                    "iand","ior","ixor","shl","shr"):
            self._lower_binop(op, instr)
        elif op.startswith("icmp."):
            self._lower_icmp(op, instr)
        elif op == "br":        self._lower_br(instr)
        elif op == "br.t":      self._lower_br_t(instr)
        elif op == "ret":       self._lower_ret(instr)
        elif op == "call":      self._lower_call(instr)
        else:
            raise ValueError(f"Unsupported IR op {op!r} in {self.func.name}")

        # Recycle registers of operands whose last use was this instruction.
        self._free_operand_registers(instr)

        # Free the result register immediately if it was allocated but is never
        # used as an operand (e.g. discarded return value of a void-like call).
        if instr.result is not None and instr.result.name not in self._last_use:
            r = self._reg.get(instr.result.name)
            if r is not None:
                self._pool.free(r)

    # ── Main entry point ──────────────────────────────────────────────────────

    def lower(self) -> list[int]:
        """Pre-scan for last uses and alloca slots, then emit instruction words."""
        self._last_use = _compute_last_use(self.func)

        # Pre-allocate all frame slots so LOAD/STORE addresses are known.
        for block in self.func.blocks:
            for instr in block.instrs:
                if instr.op == "alloca" and instr.result is not None:
                    if instr.result.name not in self._alloca:
                        self._alloc_slot(instr.result.name)

        for b_idx, block in enumerate(self.func.blocks):
            self.label_map[block.label] = self._cur_abs()
            for i_idx, instr in enumerate(block.instrs):
                self._cur_blk = b_idx
                self._cur_ins = i_idx
                self._lower_instr(instr)

        return self.words

    def resolve_fixups(self, global_labels: dict[str, int]) -> None:
        """Patch placeholder addresses.  Local labels take precedence."""
        merged = {**global_labels, **self.label_map}
        for op_idx, label in self.fixups:
            if label not in merged:
                raise ValueError(
                    f"Unresolved label {label!r} in {self.func.name}"
                )
            self.words[op_idx] = merged[label]
