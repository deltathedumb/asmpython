"""x86-64 statements to IR.

Lifting machine code to a compiler IR is normally the hard direction, and most
of the difficulty is one thing: machine registers are mutable and reassigned
constantly, so an SSA IR needs a phi at every join, which means computing
dominance frontiers before a single instruction can be emitted.

THIS IR HAS NO PHI NODES. Registers are mutable by design -- a frontend
assigns the same register on both paths where SSA would need a phi -- and that
decision, made so that beginners could write backends, is what makes a lifter
tractable. `%rax` becomes one IR register for the whole function and every
`movq ..., %rax` writes it. There is nothing to reconstruct.

The rest is four problems, each solved once here:

  * FLAGS. The IR has no flags register, and x86 splits a comparison across
    two instructions (`cmpq` then `jle`) with arbitrary code in between. So
    the last flag-setting instruction is remembered and the comparison is
    materialised at the `jcc`/`setcc` that reads it. Anything that would
    clobber flags in between invalidates the record rather than being
    ignored: an unrecoverable comparison is refused, not guessed.

  * THE FRAME. `-8(%rbp)` is a slot in a frame the prologue built. One
    `alloca` covers the whole frame and every rbp-relative access is an
    offset into it. The size comes from `subq $N, %rsp`.

  * WIDTHS. `%eax` and `%rax` are one register at two widths, and writing
    `%eax` ZEROES the top half while writing `%ax` preserves it. Both are
    modelled, because getting the first wrong yields a value that is correct
    until a number exceeds 32 bits.

  * SIGNATURES. Assembly does not record arity. A parameter is an ABI
    argument register READ BEFORE IT IS WRITTEN, which is what a parameter
    is; the return type is read the same way from the exit.

What is not supported is refused with a diagnostic naming the instruction.
A lifter that guesses produces a program that runs and is wrong, which is the
worst outcome available.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...diagnostics import DiagnosticSink, Span, error
from ...ir import Module, Op
from ...ir import types as T
from ...ir.builder import Builder
from ...ir.module import Function, Global, Instruction, Linkage
from .syntax import Directive, Imm, Instr, LabelDef, Mem, Reg, Statement, Sym

#: Argument registers in order, by ABI. A lifted function's parameters are
#: whichever of these it reads before writing.
ABI_INT_ARGS = {
    "sysv": ("rdi", "rsi", "rdx", "rcx", "r8", "r9"),
    "win64": ("rcx", "rdx", "r8", "r9"),
}
ABI_FLOAT_ARGS = {
    "sysv": tuple(f"xmm{n}" for n in range(8)),
    "win64": tuple(f"xmm{n}" for n in range(4)),
}

_SUFFIX = {"b": T.I8, "w": T.I16, "l": T.I32, "q": T.I64}

#: condition -> (opcode, signed)
_CONDITION = {
    "e": (Op.EQ, True), "z": (Op.EQ, True),
    "ne": (Op.NE, True), "nz": (Op.NE, True),
    "l": (Op.LT, True), "le": (Op.LE, True),
    "g": (Op.GT, True), "ge": (Op.GE, True),
    "b": (Op.LT, False), "be": (Op.LE, False),
    "a": (Op.GT, False), "ae": (Op.GE, False),
    "s": (Op.LT, True), "ns": (Op.GE, True),
}

_ALU = {"add": Op.ADD, "sub": Op.SUB, "imul": Op.MUL, "and": Op.AND,
        "or": Op.OR, "xor": Op.XOR, "shl": Op.SHL, "sal": Op.SHL,
        "sar": Op.SHR, "shr": Op.SHR}
_FALU = {"add": Op.ADD, "sub": Op.SUB, "mul": Op.MUL, "div": Op.DIV}

#: Instructions that leave the flags alone. Everything not listed invalidates
#: the recorded comparison -- wrong in the safe direction costs a refusal,
#: wrong the other way silently compares stale operands.
_PRESERVES_FLAGS = frozenset({
    "mov", "movq", "movl", "movw", "movb", "movabsq", "movzbq", "movzbl",
    "movzwq", "movzwl", "movsbq", "movsbl", "movswq", "movswl", "movslq",
    "lea", "leaq", "leal", "push", "pushq", "pop", "popq", "movsd", "movss",
    "movapd", "movaps", "nop", "jmp", "call", "ret", "cqto", "cltq", "cltd",
})


class LiftError(Exception):
    def __init__(self, message: str, span: Span | None = None,
                 help: str = "") -> None:
        super().__init__(message)
        self.message, self.span, self.help = message, span, help


@dataclass
class Flags:
    """What the last flag-setting instruction compared."""

    kind: str                  #: "cmp" or "test"
    lhs: int = 0
    rhs: int = 0
    signed_ty: T.Type = T.I64


@dataclass
class FuncText:
    name: str
    statements: list[Statement] = field(default_factory=list)
    exported: bool = False


def split_functions(statements: list[Statement],
                    exported: set[str]) -> list[FuncText]:
    """Carve the statement list into functions at non-local label definitions.

    A function begins at a label that is not local (`.L...`) and runs to the
    next such label. That is a convention, not a rule -- an assembler has no
    notion of a function -- but it is the one both GCC and asmpython follow,
    and the alternative is trusting `.type f, @function` directives that COFF
    does not emit at all.
    """
    out: list[FuncText] = []
    current: FuncText | None = None
    in_data = False
    for stmt in statements:
        if isinstance(stmt, Directive):
            if stmt.name in ("data", "rodata", "bss", "section"):
                in_data = not (stmt.name == "section"
                               and stmt.args and "text" in stmt.args[0])
            elif stmt.name == "text":
                in_data = False
            if current is not None:
                current.statements.append(stmt)
            continue
        if isinstance(stmt, LabelDef) and not stmt.name.startswith(".L"):
            if in_data:
                current = None
                continue
            current = FuncText(stmt.name, [], stmt.name in exported)
            out.append(current)
            continue
        if current is not None:
            current.statements.append(stmt)
    return out


class Lifter:
    """Lift one function's statements into an IR Function."""

    def __init__(self, text: FuncText, known_globals: set[str], abi: str,
                 declared: dict[str, tuple[T.Type, int]]) -> None:
        self.text = text
        self.known_globals = known_globals
        self.abi = abi
        self.declared = declared
        self.regs: dict[str, int] = {}
        self.flags: Flags | None = None
        self.frame_size = 0
        self.frame_ptr = 0
        self.spill_ptr = 0
        self.spill_depth = 0
        self.blocks: dict[str, object] = {}
        self.b: Builder | None = None

    # ── helpers over the builder ──────────────────────────────────────────
    def bin(self, op: Op, ty: T.Type, a: int, c: int) -> int:
        d = self.b.reg(ty)
        self.b.emit(Instruction(op, ty, dst=d, args=[a, c]))
        return d

    def un(self, op: Op, ty: T.Type, a: int) -> int:
        d = self.b.reg(ty)
        self.b.emit(Instruction(op, ty, dst=d, args=[a]))
        return d

    def sym_addr(self, name: str) -> int:
        d = self.b.reg(T.PTR)
        self.b.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=d, sym=name))
        return d

    # ── machine registers ─────────────────────────────────────────────────
    def machine(self, name: str) -> int:
        """The one IR register standing for a machine register.

        Every register the function mentions is created and DEFINED in the
        entry block before any of this runs, so a read here can never find an
        undefined register -- which the verifier requires on every path, and
        which a read-before-write (an incoming argument) needs anyway.
        """
        return self.regs[name]

    # ── operands ──────────────────────────────────────────────────────────
    def read(self, op, span, ty: T.Type = T.I64) -> int:
        if isinstance(op, Imm):
            if op.value is None:
                return self.sym_addr(op.symbol)
            return self.b.const(T.I64, op.value)
        if isinstance(op, Reg):
            if op.is_high_byte:
                raise LiftError(f"{op} addresses bits 8-15, which this lifter "
                                f"does not model", span)
            value = self.machine(op.name)
            if op.is_xmm or op.bits >= 64:
                return value
            mask = self.b.const(T.I64, (1 << op.bits) - 1)
            return self.bin(Op.AND, T.I64, value, mask)
        if isinstance(op, Mem):
            return self.b.load(ty, self.address(op, span))
        if isinstance(op, Sym):
            return self.sym_addr(op.name)
        raise LiftError(f"cannot read operand {op}", span)

    def write(self, op, value: int, span, ty: T.Type = T.I64) -> None:
        if isinstance(op, Reg):
            if op.is_high_byte:
                raise LiftError(f"{op} addresses bits 8-15, which this lifter "
                                f"does not model", span)
            if op.is_xmm:
                self.regs[op.name] = self._assign(op.name, value, T.F64)
                return
            if op.bits == 32:
                mask = self.b.const(T.I64, 0xFFFFFFFF)
                value = self.bin(Op.AND, T.I64, value, mask)
            elif op.bits < 32:
                # Writing %al or %ax PRESERVES the bits above it, so the old
                # value merges back in. Invisible until a register is used at
                # two widths -- which `setcc` then `movzbq` does constantly.
                old = self.machine(op.name)
                keep = self.b.const(T.I64,
                                    ~((1 << op.bits) - 1) & 0xFFFFFFFFFFFFFFFF)
                low = self.b.const(T.I64, (1 << op.bits) - 1)
                kept = self.bin(Op.AND, T.I64, old, keep)
                fresh = self.bin(Op.AND, T.I64, value, low)
                value = self.bin(Op.OR, T.I64, kept, fresh)
            self._assign(op.name, value, T.I64)
            return
        if isinstance(op, Mem):
            self.b.store(ty, value, self.address(op, span))
            return
        raise LiftError(f"cannot write operand {op}", span)

    def _assign(self, name: str, value: int, ty: T.Type) -> int:
        """Copy into the register standing for `name`.

        A COPY rather than rebinding the dict, because a machine register must
        be ONE IR register for the function's whole life -- rebinding would
        make its value depend on the path taken, which is precisely the phi
        this IR does not have.
        """
        dst = self.machine(name)
        self.b.copy(dst, value)
        return dst

    def address(self, mem: Mem, span) -> int:
        if mem.symbol and (mem.rip_relative or not mem.base):
            base = self.sym_addr(mem.symbol)
            if mem.disp:
                base = self.b.offset(base, self.b.const(T.I64, mem.disp))
        elif mem.base == "rbp":
            if not self.frame_ptr:
                raise LiftError("frame access before a prologue set up %rbp",
                                span)
            off = self.frame_size + mem.disp
            if off < 0:
                raise LiftError(
                    f"frame access {mem} lies outside the {self.frame_size} "
                    f"bytes this function reserved", span,
                    help="a lifted function may only touch its own frame")
            base = self.b.offset(self.frame_ptr, self.b.const(T.I64, off))
        elif mem.base:
            base = self.un(Op.BITCAST, T.PTR, self.machine(mem.base))
            if mem.disp:
                base = self.b.offset(base, self.b.const(T.I64, mem.disp))
        else:
            raise LiftError(f"cannot address {mem}", span)
        if mem.index:
            index = self.machine(mem.index)
            if mem.scale != 1:
                index = self.bin(Op.MUL, T.I64, index,
                                 self.b.const(T.I64, mem.scale))
            base = self.b.offset(base, index)
        return base

    # ── the walk ──────────────────────────────────────────────────────────
    def mentioned_registers(self) -> list[str]:
        """Every machine register this function names, in first-seen order."""
        seen: list[str] = []
        for stmt in self.text.statements:
            if not isinstance(stmt, Instr):
                continue
            for op in stmt.operands:
                for name in _registers_of(op):
                    if name not in seen:
                        seen.append(name)
        return seen

    def infer_parameters(self) -> list[tuple[str, T.Type]]:
        """ABI argument registers read before they are written.

        That IS what a parameter is, and it is the only evidence assembly
        carries: arity appears nowhere, and the alternative -- assume zero --
        makes every lifted call pass nothing.
        """
        written: set[str] = set()
        used: dict[str, T.Type] = {}
        for stmt in self.text.statements:
            if not isinstance(stmt, Instr):
                continue
            reads, writes = _reads_writes(stmt)
            for name in reads:
                if name not in written and name not in used:
                    used[name] = T.F64 if name.startswith("xmm") else T.I64
            written |= writes
        params = []
        for table, ty in ((ABI_INT_ARGS[self.abi], T.I64),
                          (ABI_FLOAT_ARGS[self.abi], T.F64)):
            for name in table:
                if name in used:
                    params.append((name, ty))
                else:
                    break          # arguments are consumed in order
        return params

    def run(self) -> Function:
        from ...ir.module import Block

        params = self.infer_parameters()
        fn = Function(self.text.name, T.I64,
                      linkage=(Linkage.EXPORT if self.text.exported
                               else Linkage.INTERNAL))
        entry = Block("entry")
        fn.blocks.append(entry)
        self.b = Builder(fn)

        # Parameters first: their IR registers ARE the ABI machine registers,
        # so an incoming argument needs no move.
        for name, ty in params:
            reg = fn.new_register(ty)
            fn.params.append(reg)
            self.regs[name] = reg
        # Every other register the function mentions is defined here, so no
        # path can reach a read of an undefined one.
        for name in self.mentioned_registers():
            if name in self.regs or name in ("rip", "rsp", "rbp"):
                continue
            ty = T.F64 if name.startswith(("xmm", "ymm")) else T.I64
            self.regs[name] = self.b.const(ty, 0.0 if ty is T.F64 else 0)

        self.frame_size = _frame_size(self.text.statements)
        # A little extra past the frame for push/pop, which addresses below
        # rsp and is not part of what the prologue reserved.
        self.frame_ptr = self.b.alloca(max(self.frame_size, 8) + 128)
        self.spill_ptr = self.b.offset(
            self.frame_ptr, self.b.const(T.I64, max(self.frame_size, 8)))

        # One block per label, created up front so a forward jump resolves.
        for stmt in self.text.statements:
            if isinstance(stmt, LabelDef):
                blk = Block(_label(stmt.name))
                fn.blocks.append(blk)
                self.blocks[stmt.name] = blk

        body = self.b.new_block("body")
        self.b.jump(body)
        self.b.switch_to(body)

        for stmt in self.text.statements:
            if isinstance(stmt, Directive):
                continue
            if isinstance(stmt, LabelDef):
                blk = self.blocks[stmt.name]
                if self.b.current.terminator is None:
                    self.b.jump(blk)
                self.b.switch_to(blk)
                # A label is a join: no single predecessor's comparison
                # reaches it, so anything remembered is no longer true.
                self.flags = None
                continue
            if stmt.span is not None:
                self.b.span = stmt.span
            self.instruction(stmt)

        if self.b.current.terminator is None:
            self.b.ret(self._default_return(fn))
        # A `jmp` leaves the statements after it in a block nothing reaches;
        # the verifier still requires every block to end in a terminator.
        for blk in fn.blocks:
            if blk.terminator is None:
                self.b.switch_to(blk)
                self.b.ret(self._default_return(fn))
        return fn

    def _default_return(self, fn: Function) -> int:
        name = "xmm0" if fn.ret is T.F64 else "rax"
        if name in self.regs:
            return self.regs[name]
        return self.b.const(fn.ret, 0.0 if fn.ret is T.F64 else 0)

    # ── one instruction ───────────────────────────────────────────────────
    def instruction(self, ins: Instr) -> None:
        m = ins.mnemonic
        if m not in _PRESERVES_FLAGS:
            self.flags = None
        ops = ins.operands
        span = ins.span

        # -- moves ---------------------------------------------------------
        if m in ("movq", "movl", "movw", "movb", "mov", "movabsq"):
            ty = T.I64 if m in ("mov", "movabsq") else _SUFFIX[m[-1]]
            src, dst = ops[0], ops[1]
            src_xmm = isinstance(src, Reg) and src.is_xmm
            dst_xmm = isinstance(dst, Reg) and dst.is_xmm
            if dst_xmm and not src_xmm:
                # `movq %r10, %xmm0` reinterprets the bits as a double. This
                # is how a float constant is materialised.
                self.write(dst, self.un(Op.BITCAST, T.F64,
                                        self.read(src, span)), span)
                return
            if src_xmm and not dst_xmm:
                self.write(dst, self.un(Op.BITCAST, T.I64,
                                        self.read(src, span)), span)
                return
            self.write(dst, self.read(src, span, ty), span, ty)
            return
        if m in ("movsd", "movss", "movapd", "movaps"):
            self.write(ops[1], self.read(ops[0], span, T.F64), span, T.F64)
            return
        if m.startswith("movz"):
            # movzbq etc: zero-extend, which read()'s masking already did.
            self.write(ops[1], self.read(ops[0], span), span)
            return
        if m.startswith("movs") and m not in ("movsd", "movss"):
            width = _SUFFIX.get(m[4], T.I32) if len(m) > 4 else T.I32
            value = self.read(ops[0], span)
            narrow = self.un(Op.TRUNC, width, value)
            self.write(ops[1], self.un(Op.EXTEND, T.I64, narrow), span)
            return
        if m in ("leaq", "leal", "lea"):
            self.write(ops[1], self.un(Op.BITCAST, T.I64,
                                       self.address(ops[0], span)), span)
            return

        # -- integer arithmetic --------------------------------------------
        stem = m[:-1] if m[-1] in "qlwb" and len(m) > 2 else m
        if stem in _ALU and len(ops) == 2:
            ty = _SUFFIX.get(m[-1], T.I64)
            a = self.read(ops[1], span, ty)
            c = self.read(ops[0], span, ty)
            self.write(ops[1], self.bin(_ALU[stem], T.I64, a, c), span, ty)
            return
        if stem in ("neg", "not") and len(ops) == 1:
            op = Op.NEG if stem == "neg" else Op.NOT
            self.write(ops[0], self.un(op, T.I64, self.read(ops[0], span)),
                       span)
            return
        if m in ("cqto", "cltd", "cqo"):
            return       # sign-extends rax into rdx for idiv; implicit here
        if m == "cltq":
            value = self.read(Reg("rax", 32, "eax"), span)
            self.write(Reg("rax", 64, "rax"),
                       self.un(Op.EXTEND, T.I64,
                               self.un(Op.TRUNC, T.I32, value)), span)
            return
        if stem in ("idiv", "div"):
            signed = stem == "idiv"
            ty = T.I64 if signed else T.U64
            num = self.machine("rax")
            den = self.read(ops[0], span)
            quotient = self.bin(Op.DIV, ty, num, den)
            remainder = self.bin(Op.REM, ty, num, den)
            self._assign("rax", quotient, T.I64)
            self._assign("rdx", remainder, T.I64)
            return

        # -- float arithmetic ----------------------------------------------
        if len(m) >= 5 and m[:3] in _FALU and m[3] == "s" and m[4] in "ds":
            a = self.read(ops[1], span, T.F64)
            c = self.read(ops[0], span, T.F64)
            self.write(ops[1], self.bin(_FALU[m[:3]], T.F64, a, c), span, T.F64)
            return
        if m in ("pxor", "xorps", "xorpd"):
            if len(ops) == 2 and ops[0] == ops[1]:
                self.write(ops[1], self.b.const(T.F64, 0.0), span, T.F64)
                return
            raise LiftError(f"{m} is only lifted as the zeroing idiom", span)
        if m.startswith("cvtsi2sd"):
            self.write(ops[-1], self.un(Op.ITOF, T.F64,
                                        self.read(ops[0], span)), span, T.F64)
            return
        if m.startswith(("cvttsd2si", "cvtsd2si")):
            self.write(ops[-1], self.un(Op.FTOI, T.I64,
                                        self.read(ops[0], span, T.F64)), span)
            return

        # -- flags ----------------------------------------------------------
        if m.startswith(("ucomis", "comis")):
            self.flags = Flags("cmpf", self.read(ops[1], span, T.F64),
                               self.read(ops[0], span, T.F64))
            return
        if stem == "cmp" and len(ops) == 2:
            ty = _SUFFIX.get(m[-1], T.I64)
            # AT&T order: `cmpq %b, %a` compares a AGAINST b, so the recorded
            # left-hand side is the second operand.
            self.flags = Flags("cmp", self.read(ops[1], span, ty),
                               self.read(ops[0], span, ty))
            return
        if stem == "test" and len(ops) == 2:
            self.flags = Flags("test", self.read(ops[1], span),
                               self.read(ops[0], span))
            return
        if m.startswith("set") and m[3:] in _CONDITION:
            self.write(ops[0], self.un(Op.EXTEND, T.I64,
                                       self.condition(m[3:], span)), span)
            return

        # -- control flow ---------------------------------------------------
        if m == "jmp":
            self.b.jump(self.target(ops[0], span))
            return
        if m.startswith("j") and m[1:] in _CONDITION:
            cond = self.condition(m[1:], span)
            after = self.b.new_block("after")
            self.b.branch(cond, self.target(ops[0], span), after)
            self.b.switch_to(after)
            return
        if m == "call":
            self.call(ops[0], span)
            return
        if m == "ret":
            self.b.ret(self._default_return(self.b.func))
            return

        # -- stack -----------------------------------------------------------
        if m in ("pushq", "push"):
            if isinstance(ops[0], Reg) and ops[0].name == "rbp":
                return                                     # prologue
            self.spill_depth += 8
            addr = self.b.offset(self.spill_ptr,
                                 self.b.const(T.I64, self.spill_depth))
            self.b.store(T.I64, self.read(ops[0], span), addr)
            return
        if m in ("popq", "pop"):
            if isinstance(ops[0], Reg) and ops[0].name == "rbp":
                return                                     # epilogue
            addr = self.b.offset(self.spill_ptr,
                                 self.b.const(T.I64, self.spill_depth))
            self.write(ops[0], self.b.load(T.I64, addr), span)
            self.spill_depth -= 8
            return
        if m in ("nop", "endbr64", "hlt", "ud2"):
            return

        raise LiftError(f"instruction {m!r} is not lifted", span,
                        help="this frontend implements the subset asmpython "
                             "and `gcc -O0` emit; that is outside it")

    # ── pieces ────────────────────────────────────────────────────────────
    def condition(self, cc: str, span) -> int:
        if cc not in _CONDITION:
            raise LiftError(f"condition code {cc!r} is not lifted", span)
        if self.flags is None:
            raise LiftError(
                f"cannot tell what `{cc}` tests: no comparison reaches it",
                span,
                help="either an instruction between the compare and here "
                     "clobbers the flags, or the compare is in another block")
        op, signed = _CONDITION[cc]
        f = self.flags
        if f.kind == "cmpf":
            return self.b.cmp(op, T.F64, f.lhs, f.rhs)
        if f.kind == "test":
            # `testq %r, %r` then `jne` means "r != 0". The general form ANDs
            # the two operands and compares that against zero.
            value = (f.lhs if f.lhs == f.rhs
                     else self.bin(Op.AND, T.I64, f.lhs, f.rhs))
            return self.b.cmp(op, T.I64, value, self.b.const(T.I64, 0))
        return self.b.cmp(op, T.I64 if signed else T.U64, f.lhs, f.rhs)

    def target(self, op, span):
        name = op.name if isinstance(op, Sym) else getattr(op, "symbol", None)
        if name is None or name not in self.blocks:
            raise LiftError(f"jump to {op}, which is not a label in this "
                            f"function", span)
        return self.blocks[name]

    def call(self, op, span) -> None:
        if not isinstance(op, Sym):
            raise LiftError("indirect calls are not lifted", span)
        name = op.name
        ret, argc, floats = self.declared.get(name, (T.I64, 0, ()))
        args = []
        ints = [r for r in ABI_INT_ARGS[self.abi]]
        for i in range(argc):
            reg = ABI_FLOAT_ARGS[self.abi][i] if i in floats else ints[i]
            args.append(self.machine(reg))
        d = None if ret.is_void else self.b.reg(ret)
        self.b.emit(Instruction(Op.CALL, ret, dst=d, args=args, sym=name))
        if d is not None:
            self._assign("xmm0" if ret is T.F64 else "rax", d,
                         T.F64 if ret is T.F64 else T.I64)


def _label(name: str) -> str:
    """A label the IR printer can round-trip: no leading dot, no dots."""
    return "L" + name.lstrip(".").replace(".", "_")


def _registers_of(op):
    if isinstance(op, Reg):
        yield op.name
    elif isinstance(op, Mem):
        if op.base and op.base != "rip":
            yield op.base
        if op.index:
            yield op.index


def _reads_writes(ins: Instr) -> tuple[set[str], set[str]]:
    """Which machine registers an instruction reads, and which it writes.

    Accurate enough for parameter inference and nothing else: the last operand
    of a two-operand AT&T instruction is the destination, everything before it
    is read, and `mov`/`lea`/`set` write their destination without reading it
    while every other ALU instruction reads it too.
    """
    reads: set[str] = set()
    writes: set[str] = set()
    ops = ins.operands
    if not ops:
        return reads, writes
    if ins.mnemonic == "call":
        # Argument registers are WRITTEN before a call and read by the callee.
        # Counting them as reads here would make every caller look like it
        # takes the arguments it passes.
        return reads, set(ABI_INT_ARGS["sysv"]) | set(ABI_INT_ARGS["win64"])
    for op in ops[:-1]:
        reads.update(_registers_of(op))
    last = ops[-1]
    if isinstance(last, Mem):
        reads.update(_registers_of(last))
    elif isinstance(last, Reg):
        if not ins.mnemonic.startswith(("mov", "lea", "set")):
            reads.update(_registers_of(last))
        writes.update(_registers_of(last))
    return reads, writes


def _frame_size(statements: list[Statement]) -> int:
    """How many bytes the prologue reserved, from `subq $N, %rsp`."""
    for stmt in statements:
        if (isinstance(stmt, Instr) and stmt.mnemonic in ("subq", "sub")
                and len(stmt.operands) == 2
                and isinstance(stmt.operands[0], Imm)
                and isinstance(stmt.operands[1], Reg)
                and stmt.operands[1].name == "rsp"
                and stmt.operands[0].value is not None):
            return stmt.operands[0].value
    return 0
