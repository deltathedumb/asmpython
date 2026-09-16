"""The IR type system.

Deliberately small. A type is a machine storage class and nothing more: there
are no records of what a value *means*, only how many bits it occupies and how
the machine should interpret them. Anything language-specific -- that a value is
a Python `int` rather than a C `int32_t`, that it might be boxed, that it has a
class -- belongs to a frontend and its runtime, never here.

That restraint is what makes the IR reusable. A type system that can express
"Python object" has already picked a language.

    i1                     boolean: 0 or 1, stored in one byte
    i8  i16  i32  i64      signed integers
    u8  u16  u32  u64      unsigned integers
    f32 f64                IEEE-754 floats
    ptr                    address, always 8 bytes
    void                   no value (the result type of a call that returns
                           nothing, and of every terminator)

`i1` exists so a comparison has somewhere to put its answer and `br.if` has
something to read. It occupies a whole byte -- a backend never has to think
about sub-byte storage, and the one bit of waste buys the guarantee that
`i1` behaves like every other integer type.

Signedness lives on the TYPE, and the opcode reads it. `Op.DIV` on `i32` is a
signed division; on `u32` it is unsigned. This is the opposite of the older
design, where the opcode carried the signedness (`idiv` vs `udiv`) and the type
carried it too, so the two could disagree and nothing checked.

There is no aggregate type. A struct is a `ptr` to storage you obtained from
`alloca`, and its fields are byte offsets you compute. That is a real
limitation and it is chosen: aggregates in an IR force every backend to
implement layout, calling-convention classification, and copy semantics before
it can emit its first instruction. Frontends that need layout compute it
themselves -- `layout.py` provides the C rules -- and emit loads and stores.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Type:
    """A machine storage class. Compare with `is` -- they are interned below."""

    name: str

    def __repr__(self) -> str:
        return self.name

    # ── classification ──────────────────────────────────────────────────────
    @property
    def is_int(self) -> bool:
        return self.name[0] in "iu" and self.name != "void"

    @property
    def is_signed(self) -> bool:
        """True for i8..i64. False for unsigned, floats, ptr and void.

        Read by the opcodes whose meaning depends on it: DIV, REM, SHR, the
        ordered comparisons, and the widening conversion.
        """
        return self.name[0] == "i"

    @property
    def is_float(self) -> bool:
        return self.name[0] == "f"

    @property
    def is_ptr(self) -> bool:
        return self.name == "ptr"

    @property
    def is_void(self) -> bool:
        return self.name == "void"

    @property
    def bits(self) -> int:
        """Width in bits. `ptr` is 64; `void` has no width and raises."""
        if self.name == "ptr":
            return 64
        if self.name == "void":
            raise ValueError("void has no width")
        return int(self.name[1:])

    @property
    def size(self) -> int:
        """Storage size in bytes. i1 occupies a whole byte -- see the module
        docstring for why that waste is deliberate."""
        return (self.bits + 7) // 8


I1 = Type("i1")
I8 = Type("i8")
I16 = Type("i16")
I32 = Type("i32")
I64 = Type("i64")
U8 = Type("u8")
U16 = Type("u16")
U32 = Type("u32")
U64 = Type("u64")
F32 = Type("f32")
F64 = Type("f64")
PTR = Type("ptr")
VOID = Type("void")

#: Every type, by name. The text parser and the verifier both read this, so a
#: type that is not here cannot enter the IR by any route.
ALL: dict[str, Type] = {
    t.name: t
    for t in (I1, I8, I16, I32, I64, U8, U16, U32, U64, F32, F64, PTR, VOID)
}

INTS = (I1, I8, I16, I32, I64, U8, U16, U32, U64)
FLOATS = (F32, F64)


def parse(name: str) -> Type:
    """Look up a type by name, or raise with the full list.

    The error names every valid type rather than only rejecting: a beginner
    who writes `int32` should be told what to write instead, on the spot.
    """
    try:
        return ALL[name]
    except KeyError:
        raise ValueError(
            f"unknown type {name!r}; valid types are: "
            + ", ".join(ALL)
        ) from None


def int_of(bits: int, signed: bool = True) -> Type:
    """The integer type of `bits` width, e.g. int_of(32) -> i32."""
    return parse(f"{'i' if signed else 'u'}{bits}")


def same_width(a: Type, b: Type) -> bool:
    """True if two types occupy the same number of bits."""
    return not a.is_void and not b.is_void and a.bits == b.bits
