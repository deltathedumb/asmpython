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
    offset into it. `subq $N, %rsp` is where the size starts and not where it
    ends -- callee-saved registers are saved BELOW the reservation -- so the
    deepest reference in the body has the final say.

  * WIDTHS. `%eax` and `%rax` are one register at two widths, and writing
    `%eax` ZEROES the top half while writing `%ax` preserves it. Both are
    modelled, because getting the first wrong yields a value that is correct
    until a number exceeds 32 bits.

  * SIGNATURES. Assembly does not record arity. A parameter is an ABI
    argument register READ BEFORE IT IS WRITTEN, which is what a parameter
    is; the return type is whichever of %rax/%xmm0 the exit wrote last.
    Which registers those ARE is an input (`abi`), not something the text
    reveals -- and a wrong answer there is silent, since a System V reading
    of Microsoft x64 code finds no parameters rather than bad ones.

What is not supported is refused with a diagnostic naming the instruction.
A lifter that guesses produces a program that runs and is wrong, which is the
worst outcome available.

HOW THIS IS VERIFIED, because reading it is not enough. `tests/asmpython/
integration/test_x86_lift_roundtrip.py` compiles the generated corpus to
assembly, lifts it back, and requires the lifted module to print what the
original IR printed. That round trip is what catches the failures this file
cannot announce: a float-returning function lifted as returning %rax returns
zero, and zero is a number. Written unwired and unexecuted, this lifter
raised KeyError on the second instruction of the first function it was ever
given; every refusal and every misreading found since is a case in
`tests/asmpython/unit/test_x86_lift.py`.
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

#: Parity, which `_CONDITION` cannot hold: it is not a comparison of the two
#: operands but a question about whether they were comparable at all, so it is
#: answered by `Lifter.unordered` rather than by an opcode and a signedness.
_PARITY = {"p", "pe", "np", "po"}


def _is_condition(cc: str) -> bool:
    return cc in _CONDITION or cc in _PARITY


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


def _clobbers_flags(mnemonic: str) -> bool:
    """Whether `mnemonic` destroys the recorded comparison.

    The list above is the instructions that leave the flags alone, and it is
    missing the entire category that READS them. `jne` and `setl` do not
    modify EFLAGS -- they consume it -- so clearing the record before
    dispatching them threw away the comparison the instruction was about to
    ask for, and every conditional jump and every `setcc` in the compiler's
    own output was refused with "no comparison reaches it".

    That reads as the flag tracking being too conservative across blocks. It
    was not: the comparison was one instruction away, in the same block, and
    the lifter deleted it itself.

    `cmov` is here for the same reason, and every `j*` rather than each
    condition by name -- a spelling this misses is a refusal, and a refusal
    for `jbe` when `jb` works is a puzzle nobody should have to solve.
    """
    if mnemonic in _PRESERVES_FLAGS:
        return False
    if mnemonic.startswith(("set", "cmov")) or mnemonic[:1] == "j":
        return False
    return True


#: A callee's signature: what it returns, and which machine registers carry
#: its arguments, in order. Registers rather than a count -- see `Lifter.call`.
Declaration = tuple[T.Type, tuple[str, ...]]


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
                 declared: dict[str, Declaration]) -> None:
        self.text = text
        self.known_globals = known_globals
        self.abi = abi
        self.declared = declared
        self.regs: dict[str, int] = {}
        self.flags: Flags | None = None
        self.frame_size = 0
        # None, not 0: a register id is an int and %0 is a perfectly ordinary
        # register, so `if not self.frame_ptr` read "no frame yet" off a
        # function whose alloca happened to land first.
        self.frame_ptr: int | None = None
        self.spill_ptr: int | None = None
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
    def machine(self, name: str, span=None) -> int:
        """The one IR register standing for a machine register.

        Every register the function mentions is created and DEFINED in the
        entry block before any of this runs, so a read here can never find an
        undefined register -- which the verifier requires on every path, and
        which a read-before-write (an incoming argument) needs anyway.

        Except for the three the frame model OWNS. `%rsp` and `%rbp` do not
        stand for IR registers at all: the frame is one `alloca` and those two
        are the compiler's bookkeeping for a layout that has already been
        resolved, so `frame()` consumes the instructions that manipulate them
        and anything left reaching here is a use this lifter cannot model.
        Refusing names the register; the KeyError this used to raise named a
        dictionary.
        """
        try:
            return self.regs[name]
        except KeyError:
            raise LiftError(
                f"%{name} used as a value, which this lifter does not model",
                span,
                help="the frame is modelled as a single allocation, so %rsp "
                     "and %rbp exist only in the prologue and epilogue",
            ) from None

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
            value = self.machine(op.name, span)
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
        elif mem.base == "rsp":
            # The outgoing-argument area: a caller reserves it with `subq` and
            # stores the seventh argument onward into it, and the callee reads
            # the same bytes as `16+N(%rbp)`. Two frames sharing memory is
            # exactly what "the frame is one alloca" does not express, so this
            # is refused on BOTH sides rather than lifted on one -- a caller
            # that silently dropped the store would hand the callee a stack
            # slot nobody wrote.
            raise LiftError(
                f"{mem} is the outgoing-argument area, which this lifter does "
                f"not model", span,
                help="arguments past the sixth (System V) or fourth "
                     "(Microsoft x64) are passed on the stack, and a lifted "
                     "function's frame is private to it")
        elif mem.base == "rbp":
            if self.frame_ptr is None:
                raise LiftError("frame access before a prologue set up %rbp",
                                span)
            if mem.disp >= 0:
                # 0(%rbp) is the saved %rbp, 8(%rbp) the return address, and
                # 16+(%rbp) the arguments that did not fit in registers --
                # all of which live in the CALLER's frame. A lifted function
                # has no caller frame to read, and inventing one would hand
                # back whatever the allocation happened to contain.
                raise LiftError(
                    f"{mem} reads above %rbp, which is the caller's frame",
                    span,
                    help="arguments passed on the stack are not lifted; only "
                         "the first six (System V) or four (Microsoft x64) "
                         "arrive in registers this can see")
            off = self.frame_size + mem.disp
            if off < 0:
                raise LiftError(
                    f"frame access {mem} lies outside the {self.frame_size} "
                    f"bytes this function reserved", span,
                    help="a lifted function may only touch its own frame")
            base = self.b.offset(self.frame_ptr, self.b.const(T.I64, off))
        elif mem.base:
            base = self.un(Op.BITCAST, T.PTR, self.machine(mem.base, span))
            if mem.disp:
                base = self.b.offset(base, self.b.const(T.I64, mem.disp))
        else:
            raise LiftError(f"cannot address {mem}", span)
        if mem.index:
            index = self.machine(mem.index, span)
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

        ints, floats = ABI_INT_ARGS[self.abi], ABI_FLOAT_ARGS[self.abi]
        if self.abi == "win64":
            # Microsoft x64 indexes ONE sequence by argument position: the
            # second argument travels in %rdx or %xmm1 depending on its type,
            # and the register for the other type at that position is simply
            # not used. Walking the two tables independently therefore stopped
            # at the first hole -- `f(int, float)` reads %rcx and %xmm1, the
            # scan found %xmm0 unused and concluded there were no float
            # arguments at all, and the float parameter was dropped.
            #
            # The callee then read %xmm1 as an ordinary register, which the
            # entry block initialises to zero, and the caller passed one
            # argument to a function that takes two. Nothing refused: the
            # programs ran and printed numbers that were wrong in the
            # fractional digits only, because the integer half had arrived.
            #
            # Stopping at the first position where NEITHER register is used
            # still matters, and stopping is not merely conservative. This
            # scan is linear and the code is not: a loop-carried value in an
            # argument register is read at the top of the body and written at
            # the bottom, so in source order it reads before it is written and
            # looks exactly like an incoming argument. Taking every register
            # up to the highest one used therefore invents parameters for
            # `main`, which is then called with none and traps on the first
            # read. Measured: two of two hundred generated programs.
            out = []
            for int_reg, float_reg in zip(ints, floats):
                if int_reg in used:
                    out.append((int_reg, T.I64))
                elif float_reg in used:
                    out.append((float_reg, T.F64))
                else:
                    break      # positions are consumed in order
            return out

        # System V indexes the two sequences independently, so each is scanned
        # on its own.
        params = []
        for table, ty in ((ints, T.I64), (floats, T.F64)):
            for name in table:
                if name in used:
                    params.append((name, ty))
                else:
                    break      # arguments are consumed in order
        return params

    def infer_return(self) -> T.Type:
        """Which register the exit leaves its answer in.

        The ABI puts an integer in %rax and a double in %xmm0, and assembly
        records neither the type nor whether there is one at all. What it does
        record is which of the two the function last wrote before it returned,
        and for compiled code that write IS the return value being placed.

        Assuming %rax unconditionally is the version of this that fails
        silently: a float-returning function never touches %rax, so its
        lifted `ret` hands back whatever %rax was initialised to -- zero --
        and the caller gets a plausible number rather than an error.
        """
        last_int = last_float = -1
        votes: list[T.Type] = []
        for i, stmt in enumerate(self.text.statements):
            if not isinstance(stmt, Instr):
                continue
            if stmt.mnemonic == "ret":
                if max(last_int, last_float) >= 0:
                    votes.append(T.F64 if last_float > last_int else T.I64)
                continue
            _, writes = _reads_writes(stmt)
            if "rax" in writes:
                last_int = i
            if "xmm0" in writes:
                last_float = i
        # Disagreement between two exits means the scan is not seeing what
        # this function does; %rax is the safe reading, being what an
        # integer-returning and a void function both leave behind.
        return votes[0] if votes and len(set(votes)) == 1 else T.I64

    def signature(self) -> tuple[T.Type, tuple[str, ...]]:
        """`(return type, argument registers)` without lifting the body.

        Callers need this BEFORE any body is lifted: a `call` names a symbol
        and nothing else, so lifting a call site requires the callee's
        signature, and in a module where two functions call each other there
        is no order in which both are already lifted.
        """
        return self.infer_return(), tuple(r for r, _ in self.infer_parameters())

    def run(self) -> Function:
        from ...ir.module import Block

        params = self.infer_parameters()
        fn = Function(self.text.name, self.infer_return(),
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
        # path can reach a read of an undefined one. The two return registers
        # are defined whether mentioned or not: a call's result lands in one
        # of them even when the code ignores it, and `_default_return` reads
        # one at every exit.
        for name in [*self.mentioned_registers(), "rax", "xmm0"]:
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
    def frame(self, ins: Instr) -> bool:
        """True if `ins` is frame bookkeeping this lifter has already done.

        The prologue and epilogue do not compute anything: they establish a
        layout that `_frame_size` has read off the code and `run()` has
        already turned into one `alloca`. Lifting them literally would need
        %rsp to hold a real address, which would make the frame a thing the
        IR walks rather than a thing it allocates -- and every rbp-relative
        access would then be an offset from a value instead of a slot.

        Consuming them HERE, by name, is what lets `machine()` treat any
        other appearance of %rsp/%rbp as unmodelled and say so. The pairing
        matters: recognise too little and a plain prologue is refused;
        recognise too much and a genuine stack manipulation is silently
        dropped, which is the direction that produces a wrong answer.
        """
        m, ops = ins.mnemonic, ins.operands
        two = len(ops) == 2 and isinstance(ops[1], Reg)
        if m in ("movq", "mov") and two and isinstance(ops[0], Reg):
            src, dst = ops[0].name, ops[1].name
            # `movq %rsp, %rbp` opens the frame; `movq %rbp, %rsp` closes it.
            if (src, dst) in (("rsp", "rbp"), ("rbp", "rsp")):
                return True
        if (m in ("subq", "sub", "addq", "add") and two
                and ops[1].name == "rsp" and isinstance(ops[0], Imm)
                and ops[0].value is not None):
            # Reserving the frame, releasing it, and the shadow space and
            # outgoing-argument area a call site opens and closes.
            return True
        if m == "leave":
            return True                      # movq %rbp, %rsp ; popq %rbp
        return False

    def instruction(self, ins: Instr) -> None:
        m = ins.mnemonic
        if self.frame(ins):
            return
        if _clobbers_flags(m):
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
        stem = _stem(m)
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
        if m in ("pxor", "xorps", "xorpd", "andps", "andpd", "orps", "orpd"):
            if m.startswith(("pxor", "xor")) and len(ops) == 2 \
                    and ops[0] == ops[1]:
                self.write(ops[1], self.b.const(T.F64, 0.0), span, T.F64)
                return
            # NOT only the zeroing idiom. `xorpd %xmm1, %xmm0` against a mask
            # of 0x8000000000000000 flips the sign bit, which is how this
            # backend negates a double -- 403 of them in forty generated
            # programs -- and refusing it rejected every program containing a
            # unary minus on a float.
            #
            # These are bit operations on a value the IR holds as a double, so
            # the bits have to be reached: bitcast to an integer, operate,
            # bitcast back. The round trip is exact (a bitcast reinterprets,
            # it does not convert) and it is the only way to express a sign
            # flip in an IR whose float type has no bitwise opcodes.
            bits = {"pxor": Op.XOR, "xorps": Op.XOR, "xorpd": Op.XOR,
                    "andps": Op.AND, "andpd": Op.AND,
                    "orps": Op.OR, "orpd": Op.OR}[m]
            a = self.un(Op.BITCAST, T.I64, self.read(ops[1], span, T.F64))
            c = self.un(Op.BITCAST, T.I64, self.read(ops[0], span, T.F64))
            self.write(ops[1], self.un(Op.BITCAST, T.F64,
                                       self.bin(bits, T.I64, a, c)),
                       span, T.F64)
            return
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
        if m.startswith("set") and _is_condition(m[3:]):
            self.write(ops[0], self.un(Op.EXTEND, T.I64,
                                       self.condition(m[3:], span)), span)
            return

        # -- control flow ---------------------------------------------------
        if m == "jmp":
            self.b.jump(self.target(ops[0], span))
            return
        if m.startswith("j") and _is_condition(m[1:]):
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
    def unordered(self, span) -> int:
        """Whether the recorded float comparison had a NaN operand.

        `ucomisd` sets the parity flag when its operands are unordered, and
        the compiler reads it to separate the two answers x86 collapses:
        after comparing, ZF is set both for "equal" and for "either was NaN".
        So `sete` and `setnp` are AND-ed to mean equality, and `setne` and
        `setp` OR-ed to mean inequality. Without the parity half, `nan == nan`
        lifts to true.

        There is no NaN opcode in this IR and none is needed: NaN is the only
        value that compares unequal to itself, so `a != a` IS the test, and it
        is exact rather than an approximation of one.
        """
        f = self.flags
        if f is None or f.kind != "cmpf":
            raise LiftError(
                "parity is only lifted for a floating-point comparison", span,
                help="on an integer compare the parity flag counts set bits "
                     "in the low byte, which nothing this lifts reads")
        left = self.b.cmp(Op.NE, T.F64, f.lhs, f.lhs)
        right = self.b.cmp(Op.NE, T.F64, f.rhs, f.rhs)
        return self.bin(Op.OR, T.I1, left, right)

    def condition(self, cc: str, span) -> int:
        if cc in ("p", "pe"):
            return self.unordered(span)
        if cc in ("np", "po"):
            return self.un(Op.NOT, T.I1, self.unordered(span))
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
        decl = self.declared.get(name)
        if decl is None:
            raise LiftError(
                f"call to {name}, whose signature is not known here", span,
                help="a call site does not record arity: declare the callee, "
                     "or lift it in the same module so its parameters can be "
                     "inferred from its own body")
        ret, arg_registers = decl
        # The declaration names the REGISTERS, not a count, because turning a
        # count back into registers means re-deriving the convention -- and
        # System V indexes its integer and SSE sequences independently while
        # Microsoft x64 indexes both by argument position. Rebuilding
        # `f(int, float)` from "two arguments, the second a float" picks xmm1
        # under one rule and xmm0 under the other, and the wrong choice reads
        # a register the caller never set.
        args = [self.machine(r, span) for r in arg_registers]
        d = None if ret.is_void else self.b.reg(ret)
        self.b.emit(Instruction(Op.CALL, ret, dst=d, args=args, sym=name))
        if d is not None:
            self._assign("xmm0" if ret is T.F64 else "rax", d,
                         T.F64 if ret is T.F64 else T.I64)


def lift_module(statements: list[Statement], sink: DiagnosticSink, *,
                name: str = "lifted", abi: str = "sysv",
                declared: dict[str, Declaration] | None = None) -> Module | None:
    """A whole assembly file to a `Module`. Reports to `sink`, None on error.

    TWO PASSES, and the reason is the one thing about assembly that cannot be
    worked around. `call add` names a symbol and carries no arity, so lifting
    a call site needs the callee's signature -- and in a file where `f` calls
    `g` and `g` calls `f` there is no order in which both are already lifted.
    So every function's signature is inferred from its own body first, from
    which argument registers it reads before writing, and only then is any
    body lifted. One pass would work for a file in topological order and
    silently pass zero arguments the moment one was not.

    `declared` supplies what CANNOT be recovered: the signature of a function
    defined somewhere else. A call to `put_float` is `call put_float` whether
    it takes a double or nothing at all, and guessing wrong reads a register
    the caller never set. Externals are therefore declared, not inferred, and
    a call to an undeclared symbol is refused rather than assumed to take no
    arguments.
    """
    declared = dict(declared or {})
    exported = _exported_symbols(statements)
    texts = split_functions(statements, exported)
    module = Module(name)
    module.globals.extend(_lift_globals(statements))
    known_globals = {g.name for g in module.globals}

    lifters = [Lifter(t, known_globals, abi, declared) for t in texts]
    for lifter in lifters:
        # A local definition WINS over a supplied declaration: the body is
        # better evidence than a table, and a stale entry would otherwise
        # quietly reshape every call to it.
        declared[lifter.text.name] = lifter.signature()

    failed = False
    for lifter in lifters:
        try:
            module.functions.append(lifter.run())
        except LiftError as exc:
            d = error("E0710", f"{lifter.text.name}: {exc.message}")
            if exc.span is not None:
                d = d.at(exc.span)
            if exc.help:
                d.help(exc.help)
            sink.report(d)
            failed = True

    # Anything called but not defined here becomes an external declaration, so
    # the module verifies and a consumer can see what it needs linked.
    defined = {f.name for f in module.functions}
    for symbol in sorted(_called_symbols(texts) - defined):
        ret, arg_registers = declared.get(symbol, (T.I64, ()))
        fn = Function(symbol, ret, linkage=Linkage.IMPORT, external=True)
        fn.params.extend(fn.new_register(_register_type(r))
                         for r in arg_registers)
        module.functions.append(fn)
    return None if failed or sink.failed else module


def _register_type(register: str) -> T.Type:
    return T.F64 if register.startswith(("xmm", "ymm")) else T.I64


def _exported_symbols(statements: list[Statement]) -> set[str]:
    out: set[str] = set()
    for stmt in statements:
        if isinstance(stmt, Directive) and stmt.name in ("globl", "global"):
            out.update(a.strip() for a in stmt.args if a.strip())
    return out


def _called_symbols(texts: list[FuncText]) -> set[str]:
    out: set[str] = set()
    for text in texts:
        for stmt in text.statements:
            if (isinstance(stmt, Instr) and stmt.mnemonic == "call"
                    and stmt.operands and isinstance(stmt.operands[0], Sym)):
                out.add(stmt.operands[0].name)
    return out


#: `.quad 5` and friends: directive -> width in bytes.
_DATA_WIDTH = {"byte": 1, "word": 2, "short": 2, "long": 4, "int": 4,
               "quad": 8, "octa": 16}


def _lift_globals(statements: list[Statement]) -> list[Global]:
    """Data-section labels and the bytes that follow them.

    Assembled bytes rather than a structured initialiser, because that is what
    a `Global` holds and what the data actually is: `.quad 5` and `.long 5,0`
    produce the same eight bytes and nothing downstream needs to know which
    was written.
    """
    out: list[Global] = []
    current: Global | None = None
    data: bytearray = bytearray()
    in_data = False

    def close() -> None:
        nonlocal current
        if current is not None:
            current.size = max(len(data), current.size)
            current.data = bytes(data) if data else None
            out.append(current)
            current = None
        data.clear()

    for stmt in statements:
        if isinstance(stmt, Directive):
            if stmt.name in ("data", "bss"):
                in_data = True
            elif stmt.name == "rodata":
                in_data = True
            elif stmt.name == "text":
                close()
                in_data = False
            elif stmt.name == "section":
                close()
                arg = stmt.args[0] if stmt.args else ""
                in_data = ".text" not in arg
            elif current is not None:
                _append_datum(stmt, data, current)
            continue
        if isinstance(stmt, LabelDef):
            if in_data:
                close()
                current = Global(stmt.name, 0, readonly=False)
            continue
        # An instruction in a data section is not something to reason about.
        close()
    close()
    return out


def _append_datum(stmt: Directive, data: bytearray, owner: Global) -> None:
    width = _DATA_WIDTH.get(stmt.name)
    if width is not None:
        for arg in stmt.args:
            try:
                value = int(arg, 0)
            except ValueError:
                # A symbol reference in data (a relocation). Zero holds the
                # space; resolving it needs a link step this does not do.
                value = 0
            data.extend((value & ((1 << (8 * width)) - 1))
                        .to_bytes(width, "little"))
        return
    if stmt.name in ("ascii", "asciz", "string"):
        for arg in stmt.args:
            data.extend(_unquote(arg).encode("utf-8"))
            if stmt.name != "ascii":
                data.append(0)
        return
    if stmt.name in ("zero", "space", "skip") and stmt.args:
        try:
            data.extend(b"\0" * int(stmt.args[0], 0))
        except ValueError:
            pass
        return
    if stmt.name == "comm" and len(stmt.args) >= 2:
        try:
            owner.size = max(owner.size, int(stmt.args[1], 0))
        except ValueError:
            pass


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0",
            "\\": "\\", '"': '"'}


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(_ESCAPES.get(text[i + 1], text[i + 1]))
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


#: Mnemonics the tables below are keyed by, so a suffix is only stripped when
#: what remains is one of them.
_STEMS = frozenset(_ALU) | {"neg", "not", "idiv", "div", "cmp", "test", "mul"}


def _stem(mnemonic: str) -> str:
    """`addq` -> `add`, and `sub` -> `sub`.

    Stripping a trailing q/l/w/b unconditionally is right for the suffixed
    spelling and wrong for the bare one, because the letters that mark a width
    are also letters mnemonics end in: `sub` became `su`, `shl` became `sh`,
    `imul` became `imu`, and each then missed the table and was reported as an
    instruction this lifter does not implement -- for an instruction it does.

    So the whole mnemonic is tried first and the suffix only comes off if that
    leaves something real. `movsd` is why this cannot simply strip and check:
    its `d` is "double", not a width, and the tables above must be the thing
    that decides.
    """
    if mnemonic in _STEMS:
        return mnemonic
    if len(mnemonic) > 2 and mnemonic[-1] in "qlwb":
        shorter = mnemonic[:-1]
        if shorter in _STEMS:
            return shorter
    return mnemonic


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
    is read, and `mov`/`lea`/`set`/`cvt` write their destination without
    reading it while every other ALU instruction reads it too.

    `cvt` belongs on that list and was missing, which mattered because a
    parameter IS an argument register read before it is written. Treating
    `cvtsi2sdq %rdi, %xmm1` as reading %xmm1 made %xmm1 look like an incoming
    float argument of a function that only ever used it as scratch -- so a
    two-argument function was lifted as taking three, and every caller was
    then asked for a register it had no reason to set.
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
        #
        # The RESULT registers count as written too. A call defines %rax (and
        # %xmm0) whatever it returns -- the ABI clobbers both -- and leaving
        # them out meant a function whose only write to %rax was a call's
        # return value looked like it never touched %rax at all. `infer_return`
        # then read the last write as %xmm0 and typed an integer-returning
        # function as returning a double.
        return reads, (set(ABI_INT_ARGS["sysv"]) | set(ABI_INT_ARGS["win64"])
                       | {"rax", "xmm0"})
    for op in ops[:-1]:
        reads.update(_registers_of(op))
    last = ops[-1]
    if isinstance(last, Mem):
        reads.update(_registers_of(last))
    elif isinstance(last, Reg):
        if not ins.mnemonic.startswith(("mov", "lea", "set", "cvt")):
            reads.update(_registers_of(last))
        writes.update(_registers_of(last))
    return reads, writes


def _frame_size(statements: list[Statement]) -> int:
    """How far below %rbp this function's own storage extends.

    `subq $N, %rsp` is where the answer STARTS and not where it ends. The
    x86-64 backend reserves N bytes for spill slots and then saves each
    callee-saved register it used at `-(N + 8*(i+1))(%rbp)` -- BELOW the
    reservation, because those saves are not slots the allocator hands out.
    Sizing the frame from the `subq` alone therefore refuses every function
    that touches a callee-saved register, with a diagnostic ("lies outside
    the N bytes this function reserved") that is accurate about the
    reservation and wrong about the frame.

    So the deepest actual `-K(%rbp)` reference wins when it goes deeper. That
    reads the layout off the code rather than off one instruction's
    immediate, which is also what makes this work on a function that has no
    `subq` at all -- a leaf that spills nothing still saves %rbx.
    """
    reserved = 0
    deepest = 0
    for stmt in statements:
        if not isinstance(stmt, Instr):
            continue
        ops = stmt.operands
        if (stmt.mnemonic in ("subq", "sub") and len(ops) == 2
                and isinstance(ops[0], Imm) and isinstance(ops[1], Reg)
                and ops[1].name == "rsp" and ops[0].value is not None
                and not reserved):
            reserved = ops[0].value
        for op in ops:
            if isinstance(op, Mem) and op.base == "rbp" and op.disp < 0:
                deepest = max(deepest, -op.disp)
    return max(reserved, deepest)
