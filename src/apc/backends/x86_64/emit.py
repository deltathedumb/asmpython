"""x86-64 backend: System V assembly, using the shared register allocator.

Emits GNU-syntax assembly rather than machine code. That is a deliberate
staging decision, not a shortcut: instruction SELECTION and instruction
ENCODING are independent problems, and doing them at once means a bug could be
in either. With assembly as the output, every selection decision is readable,
`as` validates the encoding, and an encoder can be added later underneath a
backend already known to select correctly.

WHAT THIS DEMONSTRATES that the C backend cannot: using `apc.backend.regalloc`.
Values live in machine registers, spilled ones in frame slots, and the prologue
saves exactly the callee-saved registers the allocation actually used.

THE ABI is System V AMD64:

    System V      args rdi rsi rdx rcx r8 r9;  callee-saved rbx r12-r15
    Microsoft x64 args rcx rdx r8 r9;          callee-saved rbx rsi rdi r12-r15,
                  and the caller reserves 32 bytes of shadow space

The ABI comes from the Target. Hardcoding one produces code that links fine on
the other platform and corrupts its arguments, which reads as a miscompilation
of the callee rather than of the call.

Floats are not implemented here. The IR has f32/f64 and this backend would need
the SSE register file and its own calling-convention class to handle them; a
backend that emits wrong float code is worse than one that refuses, so
`emit` raises on a float operation rather than pretending.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...backend.base import Backend, Target, register
from ...backend.regalloc import (
    Allocation, InRegister, InSlot, RegisterFile, allocate, verify_allocation,
)
from ...ir import Function, Module, types as T
from ...ir.module import Global, Instruction, Linkage, Register
from ...ir.opcodes import Op

@dataclass(frozen=True, slots=True)
class ABI:
    """A calling convention. Selected by target, never hardcoded.

    x86-64 has two in common use and they disagree about everything that
    matters: which registers carry arguments, which survive a call, and whether
    the caller must reserve scratch space. A backend that assumes one produces
    code that links on the other platform and corrupts its arguments -- which
    looks like a miscompilation of the callee, not of the call.
    """

    name: str
    argument_registers: tuple[str, ...]
    callee_saved: frozenset[str]
    #: Bytes the CALLER must reserve above the return address before a call.
    #: Microsoft x64 requires 32 ("shadow space") for the callee to spill its
    #: register arguments into; System V requires none.
    shadow_space: int = 0

    @property
    def allocation_order(self) -> tuple[str, ...]:
        """Caller-saved first: free in a leaf function, so the allocator only
        reaches for callee-saved (which the prologue must preserve) when the
        volatile ones run out."""
        volatile = [r for r in _ALL_GENERAL if r not in self.callee_saved]
        saved = [r for r in _ALL_GENERAL if r in self.callee_saved]
        return tuple(volatile + saved)

    def register_file(self) -> RegisterFile:
        return RegisterFile(general=self.allocation_order,
                            callee_saved=self.callee_saved,
                            reserved=RESERVED, slot_size=8)


_ALL_GENERAL = ("rax", "rcx", "rdx", "rsi", "rdi", "r8", "r9",
                "rbx", "r12", "r13", "r14", "r15")
RESERVED = frozenset({"rsp", "rbp", "r10", "r11"})

SYSTEM_V = ABI(
    "sysv",
    argument_registers=("rdi", "rsi", "rdx", "rcx", "r8", "r9"),
    callee_saved=frozenset({"rbx", "r12", "r13", "r14", "r15"}),
)
MICROSOFT_X64 = ABI(
    "win64",
    argument_registers=("rcx", "rdx", "r8", "r9"),
    # rsi and rdi are callee-saved on Windows and volatile on System V --
    # exactly the kind of difference that silently corrupts a value across a
    # call if the ABI is assumed rather than looked up.
    callee_saved=frozenset({"rbx", "rsi", "rdi", "r12", "r13", "r14", "r15"}),
    shadow_space=32,
)


def abi_for(target: Target) -> ABI:
    return MICROSOFT_X64 if "windows" in target.name else SYSTEM_V


@dataclass(frozen=True, slots=True)
class AsmDialect:
    """Object-format-specific assembler directives.

    The instructions are identical across ELF and COFF; the DIRECTIVES around
    them are not. `.type x, @function` and `.size` are ELF-only and are a hard
    error for a COFF assembler, and `.note.GNU-stack` is meaningless outside
    ELF. Emitting one set everywhere produces output that assembles on one
    platform and fails on the other with a message about a stray character.
    """

    #: Prefix applied to every symbol. COFF on 32-bit prefixed with "_";
    #: x86-64 COFF does not, but the hook belongs here rather than in the
    #: emitter, where it would be a special case in twenty places.
    symbol_prefix: str = ""

    def function_header(self, name: str, exported: bool) -> list[str]:
        raise NotImplementedError

    def function_footer(self, name: str) -> list[str]:
        return []

    def file_footer(self) -> list[str]:
        return []


class ElfDialect(AsmDialect):
    def function_header(self, name: str, exported: bool) -> list[str]:
        out = [f"	.globl {name}"] if exported else []
        return out + [f"	.type {name}, @function", f"{name}:"]

    def function_footer(self, name: str) -> list[str]:
        return [f"	.size {name}, .-{name}"]

    def file_footer(self) -> list[str]:
        # Marks the stack non-executable. Without it the linker assumes the
        # worst and marks the whole binary's stack executable.
        return ['	.section .note.GNU-stack,"",@progbits']


class CoffDialect(AsmDialect):
    def function_header(self, name: str, exported: bool) -> list[str]:
        out = [f"	.globl {name}"] if exported else []
        return out + [f"	.def {name}; .scl 2; .type 32; .endef", f"{name}:"]


def dialect_for(target: Target) -> AsmDialect:
    return CoffDialect() if target.object_format == "coff" else ElfDialect()

#: 64-bit name -> the sub-register of a given width. Needed because a `mov`
#: into `al` leaves the upper 56 bits of `rax` untouched, so a comparison
#: result must be zero-extended before it is used as a 64-bit value.
_BYTE = {
    "rax": "al", "rcx": "cl", "rdx": "dl", "rsi": "sil", "rdi": "dil",
    "rbx": "bl", "r8": "r8b", "r9": "r9b", "r10": "r10b", "r11": "r11b",
    "r12": "r12b", "r13": "r13b", "r14": "r14b", "r15": "r15b",
}

_SET_FOR = {
    (Op.EQ, True): "sete", (Op.NE, True): "setne",
    (Op.LT, True): "setl", (Op.LE, True): "setle",
    (Op.GT, True): "setg", (Op.GE, True): "setge",
    (Op.EQ, False): "sete", (Op.NE, False): "setne",
    (Op.LT, False): "setb", (Op.LE, False): "setbe",
    (Op.GT, False): "seta", (Op.GE, False): "setae",
}

_SIMPLE_BINOP = {
    Op.ADD: "addq", Op.SUB: "subq", Op.MUL: "imulq",
    Op.AND: "andq", Op.OR: "orq", Op.XOR: "xorq",
}


class UnsupportedOperation(Exception):
    """This backend cannot emit the requested operation.

    Raised rather than emitting something plausible. A backend that guesses
    produces a program that runs and is wrong, which costs far more to diagnose
    than one that refuses to build.
    """


@dataclass
class _Emitter:
    fn: Function
    alloc: Allocation
    lines: list[str] = field(default_factory=list)
    frame: int = 0

    # ── register/slot access ────────────────────────────────────────────────
    def loc(self, reg: Register) -> str:
        place = self.alloc.location(reg)
        return f"%{place.name}" if isinstance(place, InRegister) \
            else f"-{place.offset}(%rbp)"

    def into_scratch(self, reg: Register, scratch: str) -> str:
        """An operand usable as a register, loading a spilled value first."""
        place = self.alloc.location(reg)
        if isinstance(place, InRegister):
            return f"%{place.name}"
        self.emit(f"movq -{place.offset}(%rbp), %{scratch}")
        return f"%{scratch}"

    def store_from(self, scratch: str, reg: Register) -> None:
        place = self.alloc.location(reg)
        if isinstance(place, InRegister):
            if place.name != scratch:
                self.emit(f"movq %{scratch}, %{place.name}")
        else:
            self.emit(f"movq %{scratch}, -{place.offset}(%rbp)")

    def emit(self, text: str) -> None:
        self.lines.append(f"\t{text}")

    def label(self, text: str) -> None:
        self.lines.append(f"{text}:")


class X86_64Backend(Backend):
    name = "x86-64"
    description = "System V AMD64 assembly (integers only)"
    default_target = Target("x86_64-linux", object_format="elf")
    description_note = "ABI chosen by target: sysv or win64"

    def emit(self, module: Module, target: Target) -> dict[str, bytes]:
        abi = abi_for(target)
        dialect = dialect_for(target)
        out: list[str] = [
            f"# Generated by the x86-64 backend ({abi.name} ABI).",
            "# Assemble and link:  cc out.s -o out",
            "\t.text",
        ]
        for fn in module.defined_functions():
            out.extend(self._function(fn, abi, dialect))
            out.append("")

        if module.globals:
            out.append("\t.data")
            for g in module.globals:
                out.extend(self._global(g))

        out.extend(dialect.file_footer())
        return {"out.s": ("\n".join(out) + "\n").encode("utf-8")}

    # ── globals ─────────────────────────────────────────────────────────────
    def _global(self, g: Global) -> list[str]:
        lines = [f"\t.globl {g.name}"] if g.linkage is Linkage.EXPORT else []
        lines.append(f"\t.align {g.align or 8}")
        lines.append(f"{g.name}:")
        if g.data is None:
            lines.append(f"\t.zero {max(1, g.size)}")
        else:
            body = ", ".join(str(b) for b in g.data)
            lines.append(f"\t.byte {body}")
        return lines

    # ── one function ────────────────────────────────────────────────────────
    def _function(self, fn: Function, abi: ABI,
                  dialect: AsmDialect) -> list[str]:
        self._reject_floats(fn)

        alloc = allocate(fn, abi.register_file())
        problems = verify_allocation(fn, alloc)
        if problems:
            # An allocation conflict is a compiler bug, and it produces a
            # program that computes the wrong answer rather than crashing. It
            # costs microseconds to check and is never worth skipping.
            raise AssertionError(
                f"register allocation conflict in {fn.name}:\n  "
                + "\n  ".join(problems))

        e = _Emitter(fn, alloc)
        saved = sorted(alloc.used_callee_saved)
        # The frame holds spill slots plus the saved registers, kept to a
        # 16-byte multiple so the stack is aligned at any call we make.
        frame = alloc.frame_size + 8 * len(saved)
        frame = (frame + 15) & ~15
        e.frame = frame

        name = dialect.symbol_prefix + fn.name
        e.lines.extend(
            dialect.function_header(name, fn.linkage is Linkage.EXPORT))
        e.emit("pushq %rbp")
        e.emit("movq %rsp, %rbp")
        if frame:
            e.emit(f"subq ${frame}, %rsp")
        for i, reg in enumerate(saved):
            e.emit(f"movq %{reg}, -{alloc.frame_size + 8 * (i + 1)}(%rbp)")

        # Arguments arrive in ABI registers; move them where they were allocated.
        for i, param in enumerate(fn.params):
            if i >= len(abi.argument_registers):
                raise UnsupportedOperation(
                    f"{fn.name}: more than {len(abi.argument_registers)} "
                    f"parameters "
                    f"(stack arguments are not implemented)")
            e.store_from(abi.argument_registers[i], param)

        for block in fn.blocks:
            e.label(f".L_{name}_{block.label}")
            for ins in block.instructions:
                self._instruction(e, ins, saved, abi)

        e.lines.extend(dialect.function_footer(name))
        return e.lines

    @staticmethod
    def _reject_floats(fn: Function) -> None:
        for _, ins in fn.instructions():
            if ins.ty.is_float:
                raise UnsupportedOperation(
                    f"{fn.name}: this backend does not implement floating "
                    f"point ({ins.op.value} on {ins.ty}); use --backend c"
                )

    # ── one instruction ─────────────────────────────────────────────────────
    def _instruction(self, e: _Emitter, ins: Instruction, saved: list[str],
                     abi: ABI) -> None:
        op = ins.op
        fn_name = e.fn.name

        match op:
            case Op.CONST:
                e.emit(f"movq ${int(ins.imm)}, %r10")
                e.store_from("r10", ins.dst)

            case Op.COPY:
                src = e.into_scratch(ins.args[0], "r10")
                e.emit(f"movq {src}, %r10") if src != "%r10" else None
                e.store_from("r10", ins.dst)

            case Op.GLOBAL_ADDR | Op.FUNC_ADDR:
                e.emit(f"leaq {ins.sym}(%rip), %r10")
                e.store_from("r10", ins.dst)

            case _ if op in _SIMPLE_BINOP:
                a = e.into_scratch(ins.args[0], "r10")
                if a != "%r10":
                    e.emit(f"movq {a}, %r10")
                b = e.into_scratch(ins.args[1], "r11")
                e.emit(f"{_SIMPLE_BINOP[op]} {b}, %r10")
                e.store_from("r10", ins.dst)

            case Op.DIV | Op.REM:
                # idiv divides rdx:rax and clobbers both, so they are saved
                # around it -- the allocator does not know an instruction can
                # demand specific registers, and teaching it that is a much
                # larger change than spilling two registers here.
                a = e.into_scratch(ins.args[0], "r10")
                b = e.into_scratch(ins.args[1], "r11")
                e.emit("pushq %rax")
                e.emit("pushq %rdx")
                e.emit(f"movq {a}, %rax")
                if b in ("%rax", "%rdx"):
                    e.emit(f"movq {b}, %r11")
                    b = "%r11"
                if ins.ty.is_signed:
                    e.emit("cqto")
                    e.emit(f"idivq {b}")
                else:
                    e.emit("xorq %rdx, %rdx")
                    e.emit(f"divq {b}")
                e.emit(f"movq %{'rax' if op is Op.DIV else 'rdx'}, %r10")
                e.emit("popq %rdx")
                e.emit("popq %rax")
                e.store_from("r10", ins.dst)

            case Op.NEG:
                a = e.into_scratch(ins.args[0], "r10")
                if a != "%r10":
                    e.emit(f"movq {a}, %r10")
                e.emit("negq %r10")
                e.store_from("r10", ins.dst)

            case Op.NOT:
                a = e.into_scratch(ins.args[0], "r10")
                if a != "%r10":
                    e.emit(f"movq {a}, %r10")
                e.emit("notq %r10")
                e.store_from("r10", ins.dst)

            case Op.SHL | Op.SHR:
                a = e.into_scratch(ins.args[0], "r10")
                if a != "%r10":
                    e.emit(f"movq {a}, %r10")
                b = e.into_scratch(ins.args[1], "r11")
                e.emit("pushq %rcx")
                e.emit(f"movq {b}, %rcx")
                mnemonic = "shlq" if op is Op.SHL else (
                    "sarq" if ins.ty.is_signed else "shrq")
                e.emit(f"{mnemonic} %cl, %r10")
                e.emit("popq %rcx")
                e.store_from("r10", ins.dst)

            case Op.EQ | Op.NE | Op.LT | Op.LE | Op.GT | Op.GE:
                a = e.into_scratch(ins.args[0], "r10")
                if a != "%r10":
                    e.emit(f"movq {a}, %r10")
                b = e.into_scratch(ins.args[1], "r11")
                e.emit(f"cmpq {b}, %r10")
                e.emit(f"{_SET_FOR[(op, ins.ty.is_signed)]} %r10b")
                # setcc writes one byte; the upper 56 bits keep whatever was
                # there. Without this the i1 is not 0 or 1 and every later test
                # of it is wrong.
                e.emit("movzbq %r10b, %r10")
                e.store_from("r10", ins.dst)

            case Op.TRUNC | Op.EXTEND | Op.BITCAST:
                a = e.into_scratch(ins.args[0], "r10")
                if a != "%r10":
                    e.emit(f"movq {a}, %r10")
                width = ins.ty.bits
                if op is Op.TRUNC and width < 64:
                    e.emit(f"andq ${(1 << width) - 1}, %r10")
                e.store_from("r10", ins.dst)

            case Op.ALLOCA:
                size = (int(ins.imm) + 15) & ~15
                e.emit(f"subq ${size}, %rsp")
                e.emit("movq %rsp, %r10")
                e.store_from("r10", ins.dst)

            case Op.LOAD:
                addr = e.into_scratch(ins.args[0], "r11")
                e.emit(f"movq ({addr}), %r10")
                e.store_from("r10", ins.dst)

            case Op.STORE:
                value = e.into_scratch(ins.args[0], "r10")
                if value != "%r10":
                    e.emit(f"movq {value}, %r10")
                addr = e.into_scratch(ins.args[1], "r11")
                e.emit(f"movq %r10, ({addr})")

            case Op.OFFSET:
                base = e.into_scratch(ins.args[0], "r10")
                if base != "%r10":
                    e.emit(f"movq {base}, %r10")
                off = e.into_scratch(ins.args[1], "r11")
                e.emit(f"addq {off}, %r10")
                e.store_from("r10", ins.dst)

            case Op.CALL:
                if len(ins.args) > len(abi.argument_registers):
                    raise UnsupportedOperation(
                        f"call to {ins.sym} with {len(ins.args)} arguments "
                        f"(stack arguments are not implemented)")
                for i, arg in enumerate(ins.args):
                    src = e.into_scratch(arg, "r10")
                    e.emit(f"movq {src}, %{abi.argument_registers[i]}")
                if abi.shadow_space:
                    e.emit(f"subq ${abi.shadow_space}, %rsp")
                e.emit(f"call {ins.sym}")
                if abi.shadow_space:
                    e.emit(f"addq ${abi.shadow_space}, %rsp")
                if ins.dst is not None:
                    e.store_from("rax", ins.dst)

            case Op.JUMP:
                e.emit(f"jmp .L_{fn_name}_{ins.labels[0]}")

            case Op.BRANCH:
                cond = e.into_scratch(ins.args[0], "r10")
                e.emit(f"testq {cond}, {cond}")
                e.emit(f"jne .L_{fn_name}_{ins.labels[0]}")
                e.emit(f"jmp .L_{fn_name}_{ins.labels[1]}")

            case Op.SWITCH:
                value = e.into_scratch(ins.args[0], "r10")
                for case_value, target in ins.cases:
                    e.emit(f"cmpq ${case_value}, {value}")
                    e.emit(f"je .L_{fn_name}_{target}")
                e.emit(f"jmp .L_{fn_name}_{ins.labels[0]}")

            case Op.RET:
                if ins.args:
                    src = e.into_scratch(ins.args[0], "r10")
                    e.emit(f"movq {src}, %rax")
                for i, reg in enumerate(saved):
                    e.emit(f"movq -{e.alloc.frame_size + 8 * (i + 1)}(%rbp), %{reg}")
                e.emit("movq %rbp, %rsp")
                e.emit("popq %rbp")
                e.emit("ret")

            case Op.UNREACHABLE:
                e.emit("ud2")

            case _:
                raise UnsupportedOperation(
                    f"x86-64 backend has no rule for {op.value!r}")


register(X86_64Backend())
