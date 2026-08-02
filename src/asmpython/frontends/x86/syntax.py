"""What an x86-64 assembly line can say.

The parser produces these and nothing else, so the lifter never sees text.
That split is worth stating because assembly lifters usually skip it and
pattern-match strings: `if line.startswith("mov")` is fine until `movsd`,
`movzbq` and `movabsq` all arrive, and by then the matching is spread across
the lifter and cannot be tested on its own.

AT&T ORDER IS SOURCE-FIRST. `movq %rax, %rbx` moves rax INTO rbx, the reverse
of Intel syntax, and `subq %rax, %rbx` computes rbx - rax. Every operand list
here is stored exactly as written, and the lifter is where that is turned
around -- one place, named, rather than a reversal remembered at each use.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...diagnostics import Span

# ── registers ──────────────────────────────────────────────────────────────
#
# A machine register is a NAME and a WIDTH, and the two are tangled: `%al`,
# `%ax`, `%eax` and `%rax` are one register read at four widths, while `%ah`
# is a fifth thing that is not the low bits of anything. The table below is
# exhaustive on purpose -- deriving the family from the spelling works for
# `r8b`/`r8w`/`r8d`/`r8` and fails for every legacy name.

_GP_FAMILIES = {
    "rax": ("al", "ax", "eax", "rax"),
    "rbx": ("bl", "bx", "ebx", "rbx"),
    "rcx": ("cl", "cx", "ecx", "rcx"),
    "rdx": ("dl", "dx", "edx", "rdx"),
    "rsi": ("sil", "si", "esi", "rsi"),
    "rdi": ("dil", "di", "edi", "rdi"),
    "rbp": ("bpl", "bp", "ebp", "rbp"),
    "rsp": ("spl", "sp", "esp", "rsp"),
    **{f"r{n}": (f"r{n}b", f"r{n}w", f"r{n}d", f"r{n}")
       for n in range(8, 16)},
}

#: spelling -> (canonical 64-bit name, width in bits)
REGISTERS: dict[str, tuple[str, int]] = {}
for _canon, _names in _GP_FAMILIES.items():
    for _bits, _name in zip((8, 16, 32, 64), _names):
        REGISTERS[_name] = (_canon, _bits)
#: `%ah` and friends address bits 8-15. Nothing asmpython emits uses them and
#: nothing here lifts them, but they must PARSE -- an unknown register and an
#: unsupported one are different diagnostics, and only one is the user's fault.
for _name, _canon in (("ah", "rax"), ("bh", "rbx"),
                      ("ch", "rcx"), ("dh", "rdx")):
    REGISTERS[_name] = (_canon, -8)          # -8 marks "the high byte"
for _n in range(16):
    REGISTERS[f"xmm{_n}"] = (f"xmm{_n}", 128)
    REGISTERS[f"ymm{_n}"] = (f"ymm{_n}", 256)
REGISTERS["rip"] = ("rip", 64)

GP64 = tuple(_GP_FAMILIES)
XMM = tuple(f"xmm{n}" for n in range(16))


def canonical(name: str) -> tuple[str, int]:
    """`("r10b")` -> `("r10", 8)`. Raises KeyError for a non-register."""
    return REGISTERS[name]


# ── operands ───────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class Reg:
    """A register operand, at the width it was written."""

    name: str                 #: canonical 64-bit name, e.g. "r10"
    bits: int                 #: 8, 16, 32, 64 for GP; 128 for xmm; -8 for %ah
    spelling: str = ""        #: as written, for diagnostics

    @property
    def is_xmm(self) -> bool:
        return self.name.startswith(("xmm", "ymm"))

    @property
    def is_high_byte(self) -> bool:
        return self.bits == -8

    def __str__(self) -> str:
        return f"%{self.spelling or self.name}"


@dataclass(frozen=True, slots=True)
class Imm:
    """`$5`, or `$symbol`."""

    value: int | None         #: None when the immediate is a symbol
    symbol: str | None = None

    def __str__(self) -> str:
        return f"${self.symbol if self.value is None else self.value}"


@dataclass(frozen=True, slots=True)
class Mem:
    """`disp(base, index, scale)`, `symbol(%rip)`, or `(%rax)`.

    Every field is optional and the combinations are not all meaningful; the
    parser accepts what the syntax allows and the lifter rejects what it
    cannot lower. Validating here would mean the parser deciding what a
    lifter can do, which is exactly the coupling the split avoids.
    """

    disp: int = 0
    base: str | None = None       #: canonical 64-bit register name
    index: str | None = None
    scale: int = 1
    symbol: str | None = None
    #: True for `sym(%rip)`, which addresses relative to the next instruction
    #: rather than to a register that holds an address.
    rip_relative: bool = False

    def __str__(self) -> str:
        inner = ""
        if self.base or self.index:
            parts = [f"%{self.base}" if self.base else ""]
            if self.index:
                parts += [f"%{self.index}", str(self.scale)]
            inner = "(" + ",".join(parts) + ")"
        head = self.symbol or (str(self.disp) if self.disp or not inner else "")
        return f"{head}{inner}"


@dataclass(frozen=True, slots=True)
class Sym:
    """A bare symbol: a jump target, or a call destination."""

    name: str

    def __str__(self) -> str:
        return self.name


Operand = Reg | Imm | Mem | Sym


# ── statements ─────────────────────────────────────────────────────────────

@dataclass(slots=True)
class Instr:
    """One instruction.

    `mnemonic` keeps its suffix (`movq`, not `mov` + `q`) because the suffix
    is not always a width -- `movzbq` carries two, `cqto` carries none, and
    `movsd` means "scalar double" rather than "move, suffix d". Splitting it
    here would be a guess; the lifter's table knows which is which.
    """

    mnemonic: str
    operands: list[Operand] = field(default_factory=list)
    span: Span | None = None

    def __str__(self) -> str:
        return f"{self.mnemonic} {', '.join(map(str, self.operands))}".strip()


@dataclass(slots=True)
class LabelDef:
    name: str
    span: Span | None = None


@dataclass(slots=True)
class Directive:
    """`.globl main`, `.quad 5`, `.asciz "hi"`.

    Arguments are kept as raw text rather than parsed into operands: a
    directive's arguments follow no shared grammar (`.def` takes a symbol,
    `.align` a number, `.asciz` a C string, `.section` a name and flags), and
    a parser that pretended otherwise would have to be re-taught for each one.
    """

    name: str
    args: list[str] = field(default_factory=list)
    span: Span | None = None

    def __str__(self) -> str:
        return f".{self.name} {', '.join(self.args)}".strip()


Statement = Instr | LabelDef | Directive
