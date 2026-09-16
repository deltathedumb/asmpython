"""The C type system: what a type IS, how big it is, and how two of them meet.

THIS IS NOT THE IR'S TYPE SYSTEM AND MUST NOT BECOME IT. `ir/types.py` holds
thirteen machine storage classes and says, at length, that a type system able
to express "Python object" has already picked a language. A type system able to
express `const char *restrict` has picked C. So C's types live here, the IR's
live there, and `to_ir` below is the one-way door between them -- one-way
because `i32` cannot tell you whether it was an `int`, an `enum`, a `long` on
another target, or four bytes of a struct.

THE TARGET IS LP64: 8/16/32/64/64 for char/short/int/long/long long, 64-bit
pointers, little-endian. Written out in `_WIDTH` rather than derived, because
every one of those is a decision a different target makes differently and a
table is where you change them.

`long double` IS `double`. The IR has `f32` and `f64` and nothing wider, so an
80-bit extended type has nowhere to live -- and silently giving `long double`
the range of `double` while claiming otherwise in `<float.h>` would make
`LDBL_MAX` a lie a program can print. It is documented, `__SIZEOF_LONG_DOUBLE__`
says 8, and `<float.h>` gives the `LDBL_*` macros `double`'s values.

A TAG IS MUTABLE AND A TYPE IS NOT. `struct list { struct list *next; };` needs
the type to exist before its own members do, so `CType` is frozen and holds a
reference to a `Tag`, which is filled in later. That is also what makes
`struct s;` -- an incomplete type you may point at and not dereference -- a
`Tag` with `complete = False` rather than a separate kind of type.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from typing import Any

from ...ir import types as IR


class K(enum.Enum):
    """A type's kind. Ordered by integer conversion rank where that applies,
    which is what `rank()` reads rather than keeping a second table."""

    VOID = "void"
    BOOL = "_Bool"
    CHAR = "char"
    SCHAR = "signed char"
    UCHAR = "unsigned char"
    SHORT = "short"
    USHORT = "unsigned short"
    INT = "int"
    UINT = "unsigned int"
    LONG = "long"
    ULONG = "unsigned long"
    LLONG = "long long"
    ULLONG = "unsigned long long"
    FLOAT = "float"
    DOUBLE = "double"
    LDOUBLE = "long double"
    POINTER = "pointer"
    ARRAY = "array"
    FUNCTION = "function"
    STRUCT = "struct"
    UNION = "union"
    ENUM = "enum"


#: kind -> (bytes, signed). Pointers and aggregates are not here: their size is
#: computed. See the module docstring on why this is a table.
_SCALAR: dict[K, tuple[int, bool]] = {
    K.VOID: (1, False),     # gcc's `sizeof(void) == 1`; arithmetic on void*
    K.BOOL: (1, False),
    K.CHAR: (1, True),      # plain char is SIGNED here; see literals.PREFIXES
    K.SCHAR: (1, True),
    K.UCHAR: (1, False),
    K.SHORT: (2, True),
    K.USHORT: (2, False),
    K.INT: (4, True),
    K.UINT: (4, False),
    K.LONG: (8, True),
    K.ULONG: (8, False),
    K.LLONG: (8, True),
    K.ULLONG: (8, False),
    K.FLOAT: (4, True),
    K.DOUBLE: (8, True),
    K.LDOUBLE: (8, True),
}

#: Integer conversion rank (6.3.1.1). Equal-rank signed and unsigned types
#: share a number, which is exactly what the usual arithmetic conversions need
#: to ask about.
_RANK: dict[K, int] = {
    K.BOOL: 0, K.CHAR: 1, K.SCHAR: 1, K.UCHAR: 1,
    K.SHORT: 2, K.USHORT: 2, K.INT: 3, K.UINT: 3,
    K.LONG: 4, K.ULONG: 4, K.LLONG: 5, K.ULLONG: 5,
}

#: signed kind -> unsigned kind, for the conversions that need the other one.
_TO_UNSIGNED: dict[K, K] = {
    K.CHAR: K.UCHAR, K.SCHAR: K.UCHAR, K.SHORT: K.USHORT, K.INT: K.UINT,
    K.LONG: K.ULONG, K.LLONG: K.ULLONG, K.BOOL: K.BOOL,
}

_INTEGER = frozenset({K.BOOL, K.CHAR, K.SCHAR, K.UCHAR, K.SHORT, K.USHORT,
                      K.INT, K.UINT, K.LONG, K.ULONG, K.LLONG, K.ULLONG})
_FLOATING = frozenset({K.FLOAT, K.DOUBLE, K.LDOUBLE})
_QUALIFIERS = ("const", "volatile", "restrict", "_Atomic")


@dataclass(slots=True)
class Member:
    """One member of a struct or union, after layout."""

    name: str | None            #: None for an anonymous struct/union member
    type: CType
    offset: int = 0
    #: Bit-field width, or None. `bit_offset` is from the start of `offset`.
    bits: int | None = None
    bit_offset: int = 0
    span: Any = None
    #: For an anonymous member, the path to reach a named member inside it is
    #: rebuilt on demand rather than flattened here -- see `find_member`.

    @property
    def is_bitfield(self) -> bool:
        return self.bits is not None


@dataclass(slots=True)
class Tag:
    """A struct, union or enum's identity. Mutable; the type is not.

    TWO DECLARATIONS OF ONE TAG IN ONE SCOPE ARE ONE TAG, which is why this is
    shared by reference rather than copied: `struct s;` followed by
    `struct s { int x; };` completes the first one, and every type that already
    pointed at it sees the members appear.
    """

    kind: K
    name: str | None
    members: list[Member] = field(default_factory=list)
    complete: bool = False
    size: int = 0
    align: int = 1
    #: ENUM only: the type the enumeration's values are stored in.
    base: CType | None = None
    #: ENUM only: name -> value, in declaration order.
    values: dict[str, int] = field(default_factory=dict)
    span: Any = None
    #: True once a flexible array member has been seen, so a later member or a
    #: second flexible one can be refused by name.
    flexible: bool = False
    #: Set while layout is running, so a struct containing itself is caught
    #: here rather than by a recursion limit.
    laying_out: bool = False

    def __repr__(self) -> str:
        return f"<{self.kind.value} {self.name or '(anonymous)'}>"


@dataclass(frozen=True, slots=True)
class Param:
    name: str | None
    type: CType
    span: Any = None


@dataclass(frozen=True, slots=True)
class CType:
    """One C type. Frozen: two identical types are interchangeable."""

    kind: K
    #: Qualifiers on THIS type. `const char *` and `char *const` differ by
    #: which of the two types in the chain carries the `const`.
    qual: frozenset[str] = frozenset()
    #: POINTER and ARRAY: what it points at / holds.
    of: CType | None = None
    #: ARRAY: the element count, or None for `[]`.
    count: int | None = None
    #: ARRAY: the expression of a variable-length array, unevaluated.
    vla: Any = None
    #: FUNCTION: the return type and parameters. `params is None` is a
    #: declaration with no prototype -- `int f();` -- which is NOT `(void)`.
    ret: CType | None = None
    params: tuple[Param, ...] | None = None
    variadic: bool = False
    #: STRUCT, UNION, ENUM.
    tag: Tag | None = None
    #: A typedef's name, kept for diagnostics only: a message reading
    #: `size_t` rather than `unsigned long` is the one the programmer wrote.
    alias: str | None = None

    # ── classification ──────────────────────────────────────────────────────
    @property
    def is_void(self) -> bool: return self.kind is K.VOID

    @property
    def is_bool(self) -> bool: return self.kind is K.BOOL

    @property
    def is_integer(self) -> bool:
        return self.kind in _INTEGER or self.kind is K.ENUM

    @property
    def is_float(self) -> bool: return self.kind in _FLOATING

    @property
    def is_arithmetic(self) -> bool: return self.is_integer or self.is_float

    @property
    def is_pointer(self) -> bool: return self.kind is K.POINTER

    @property
    def is_array(self) -> bool: return self.kind is K.ARRAY

    @property
    def is_function(self) -> bool: return self.kind is K.FUNCTION

    @property
    def is_record(self) -> bool: return self.kind in (K.STRUCT, K.UNION)

    @property
    def is_enum(self) -> bool: return self.kind is K.ENUM

    @property
    def is_scalar(self) -> bool: return self.is_arithmetic or self.is_pointer

    @property
    def is_aggregate(self) -> bool:
        return self.kind is K.STRUCT or self.is_array

    @property
    def is_vla(self) -> bool:
        return self.kind is K.ARRAY and self.vla is not None

    @property
    def signed(self) -> bool:
        """Whether values of this type are signed. Enums follow their base."""
        if self.kind is K.ENUM:
            return self.tag.base.signed if self.tag and self.tag.base else True
        return _SCALAR.get(self.kind, (0, False))[1]

    @property
    def is_const(self) -> bool: return "const" in self.qual

    @property
    def is_volatile(self) -> bool: return "volatile" in self.qual

    # ── size and alignment ──────────────────────────────────────────────────
    @property
    def complete(self) -> bool:
        """Whether the type's size is known. `void` never is; an array of
        unknown bound and an undefined tag are not yet."""
        if self.kind is K.VOID or self.kind is K.FUNCTION:
            return False
        if self.kind is K.ARRAY:
            return (self.count is not None or self.vla is not None) \
                and self.of is not None and self.of.complete
        if self.tag is not None:
            return self.tag.complete
        return True

    @property
    def size(self) -> int:
        """Bytes. Raises on an incomplete type -- the caller must have checked."""
        if self.kind in _SCALAR:
            return _SCALAR[self.kind][0]
        if self.kind is K.POINTER:
            return 8
        if self.kind is K.ENUM:
            return self.tag.base.size if self.tag and self.tag.base else 4
        if self.kind is K.ARRAY:
            if self.count is None:
                raise IncompleteType(self)
            return self.of.size * self.count
        if self.kind is K.FUNCTION:
            # gcc gives a function type size 1 so `sizeof f` compiles; the
            # standard makes it a constraint violation. Refused, and the
            # caller turns this into a diagnostic naming `sizeof`.
            raise IncompleteType(self)
        if self.tag is not None:
            if not self.tag.complete:
                raise IncompleteType(self)
            return self.tag.size
        raise IncompleteType(self)

    @property
    def align(self) -> int:
        if self.kind in _SCALAR:
            return _SCALAR[self.kind][0]
        if self.kind is K.POINTER:
            return 8
        if self.kind is K.ENUM:
            return self.tag.base.align if self.tag and self.tag.base else 4
        if self.kind is K.ARRAY:
            return self.of.align
        if self.tag is not None and self.tag.complete:
            return self.tag.align
        raise IncompleteType(self)

    @property
    def bits(self) -> int:
        return self.size * 8

    # ── qualifiers ──────────────────────────────────────────────────────────
    def unqualified(self) -> CType:
        return self if not self.qual else replace(self, qual=frozenset())

    def qualified(self, quals) -> CType:
        quals = frozenset(quals)
        return self if quals <= self.qual else replace(self, qual=self.qual | quals)

    def named(self, alias: str | None) -> CType:
        return replace(self, alias=alias)

    def __str__(self) -> str:
        return spell(self)

    def __repr__(self) -> str:
        return f"CType({spell(self)})"


class IncompleteType(Exception):
    """`sizeof` of something whose size is not known. Carries the type so the
    caller can name it: "invalid use of incomplete type `struct s`"."""

    def __init__(self, ty: CType) -> None:
        self.type = ty
        super().__init__(f"incomplete type {spell(ty)}")


# ── the interned basic types ────────────────────────────────────────────────
VOID = CType(K.VOID)
BOOL = CType(K.BOOL)
CHAR = CType(K.CHAR)
SCHAR = CType(K.SCHAR)
UCHAR = CType(K.UCHAR)
SHORT = CType(K.SHORT)
USHORT = CType(K.USHORT)
INT = CType(K.INT)
UINT = CType(K.UINT)
LONG = CType(K.LONG)
ULONG = CType(K.ULONG)
LLONG = CType(K.LLONG)
ULLONG = CType(K.ULLONG)
FLOAT = CType(K.FLOAT)
DOUBLE = CType(K.DOUBLE)
LDOUBLE = CType(K.LDOUBLE)

#: The types the language names for itself. `size_t` is `unsigned long` and
#: `ptrdiff_t` is `long`, which `<stddef.h>` repeats and the predefined
#: `__SIZE_TYPE__` macro tells a third-party header.
SIZE_T = ULONG
PTRDIFF_T = LONG
WCHAR_T = INT
CHAR16_T = USHORT
CHAR32_T = UINT
VOID_PTR = CType(K.POINTER, of=VOID)
CHAR_PTR = CType(K.POINTER, of=CHAR)
CONST_CHAR_PTR = CType(K.POINTER, of=CHAR.qualified({"const"}))

#: The type a `char` string literal has: `char[n]`, not `const char[n]` -- C
#: differs from C++ here, and a program assigning one to a `char *` is legal.
def array_of(elem: CType, count: int | None, vla: Any = None) -> CType:
    return CType(K.ARRAY, of=elem, count=count, vla=vla)


def pointer_to(target: CType, quals=frozenset()) -> CType:
    return CType(K.POINTER, of=target, qual=frozenset(quals))


def function(ret: CType, params: tuple[Param, ...] | None,
             variadic: bool = False) -> CType:
    return CType(K.FUNCTION, ret=ret, params=params, variadic=variadic)


def record(tag: Tag) -> CType:
    return CType(tag.kind, tag=tag)


def integer(bits: int, signed: bool) -> CType:
    """The C type of a given width. Used by `<stdint.h>`'s own lowering and by
    anything that has computed a width and needs a type back."""
    table = {(8, True): SCHAR, (8, False): UCHAR, (16, True): SHORT,
             (16, False): USHORT, (32, True): INT, (32, False): UINT,
             (64, True): LONG, (64, False): ULONG}
    return table[(bits, signed)]


# ── conversions ─────────────────────────────────────────────────────────────
def rank(ty: CType) -> int:
    if ty.kind is K.ENUM:
        return _RANK[(ty.tag.base.kind if ty.tag and ty.tag.base else K.INT)]
    return _RANK.get(ty.kind, 0)


def promote(ty: CType) -> CType:
    """The integer promotions, 6.3.1.1p2.

    EVERY type whose rank is below `int` becomes `int` if `int` can hold all
    its values, and `unsigned int` otherwise. With 32-bit `int` and no
    16-bit-int target, the second case never arises for the standard types --
    but a bit-field of 32 bits declared `unsigned` does hit it, which is why
    the rule is written out rather than shortcut to "becomes int".
    """
    if not ty.is_integer:
        return ty
    if ty.kind is K.ENUM:
        base = ty.tag.base if ty.tag and ty.tag.base else INT
        return promote(base)
    if rank(ty) >= _RANK[K.INT]:
        return ty.unqualified()
    if not ty.signed and ty.size >= INT.size:
        return UINT
    return INT


def promote_bitfield(ty: CType, width: int) -> CType:
    """A bit-field's promoted type, which depends on its WIDTH and not only on
    its declared type: `unsigned x : 3` promotes to `int`, because every value
    of a 3-bit unsigned field fits in one."""
    if not ty.is_integer:
        return ty
    if width < INT.bits:
        return INT
    if width == INT.bits and ty.signed:
        return INT
    if width == INT.bits:
        return UINT
    return promote(ty)


def usual_arithmetic(a: CType, b: CType) -> CType:
    """The usual arithmetic conversions, 6.3.1.8. The common type of a binary
    arithmetic operator's operands."""
    if a.is_float or b.is_float:
        for k in (K.LDOUBLE, K.DOUBLE, K.FLOAT):
            if a.kind is k or b.kind is k:
                return CType(k)
    a, b = promote(a), promote(b)
    if a.kind is b.kind:
        return a.unqualified()
    ra, rb = rank(a), rank(b)
    if a.signed == b.signed:
        return a if ra > rb else b
    # One signed, one not. The unsigned one wins unless the signed one is
    # strictly wider -- which is the rule that makes `-1 < 0u` false and is
    # the single most surprising line in C's arithmetic.
    u, s = (a, b) if not a.signed else (b, a)
    ru, rs = (ra, rb) if not a.signed else (rb, ra)
    if ru >= rs:
        return u
    if s.size > u.size:
        return s
    return CType(_TO_UNSIGNED[s.kind])


def decay(ty: CType) -> CType:
    """An array becomes a pointer to its first element; a function becomes a
    pointer to itself. 6.3.2.1p3-4, and the reason `sizeof` needs the
    UNDECAYED type -- which is why this is a function you call rather than
    something the parser does."""
    if ty.is_array:
        # THE ELEMENT'S QUALIFIERS COME FROM THE ARRAY. `const int a[4]` is an
        # array of `const int`, so `a` decays to `const int *`; the qualifier
        # is written on the array in the declaration and belongs on the
        # element in the type.
        elem = ty.of.qualified(ty.qual) if ty.qual else ty.of
        return pointer_to(elem)
    if ty.is_function:
        return pointer_to(ty)
    return ty


def to_ir(ty: CType) -> IR.Type:
    """The IR storage class a value of `ty` lives in.

    ONE-WAY. Everything C knows that the machine does not -- which integer
    type, which struct, what it is called -- is discarded here, which is the
    point: the IR is not supposed to be able to say `struct point`. An
    aggregate has no IR type at all and is always handled as a `ptr` to its
    storage; asking for one is a bug in the caller, so it raises.
    """
    k = ty.kind
    if k is K.BOOL:
        return IR.I1
    if k in _INTEGER:
        size, signed = _SCALAR[k]
        return IR.int_of(size * 8, signed)
    if k is K.ENUM:
        return to_ir(ty.tag.base if ty.tag and ty.tag.base else INT)
    if k is K.FLOAT:
        return IR.F32
    if k in (K.DOUBLE, K.LDOUBLE):
        return IR.F64
    if k in (K.POINTER, K.ARRAY, K.FUNCTION):
        return IR.PTR
    if k is K.VOID:
        return IR.VOID
    raise ValueError(f"{spell(ty)} has no IR type; aggregates live in memory")


# ── compatibility ───────────────────────────────────────────────────────────
def compatible(a: CType, b: CType, *, qualifiers: bool = True) -> bool:
    """Type compatibility, 6.2.7. Not equality: `int[]` and `int[4]` are
    compatible, and so are `int f()` and `int f(int)`."""
    if a is b:
        return True
    if qualifiers and a.qual != b.qual:
        return False
    if a.kind is not b.kind:
        # An enum is compatible with its own base type, which is what lets
        # `enum e` and `int` be interchanged in a prototype.
        if a.is_enum and b.is_integer:
            return compatible(a.tag.base or INT, b, qualifiers=qualifiers)
        if b.is_enum and a.is_integer:
            return compatible(a, b.tag.base or INT, qualifiers=qualifiers)
        return False
    if a.kind in (K.STRUCT, K.UNION, K.ENUM):
        return a.tag is b.tag
    if a.kind is K.POINTER:
        return compatible(a.of, b.of)
    if a.kind is K.ARRAY:
        if a.count is not None and b.count is not None and a.count != b.count:
            return False
        return compatible(a.of, b.of)
    if a.kind is K.FUNCTION:
        if not compatible(a.ret, b.ret):
            return False
        if a.params is None or b.params is None:
            return True             # one has no prototype; 6.7.6.3p15
        if a.variadic != b.variadic or len(a.params) != len(b.params):
            return False
        return all(compatible(decay(p.type).unqualified(),
                              decay(q.type).unqualified())
                   for p, q in zip(a.params, b.params))
    return True


def composite(a: CType, b: CType) -> CType:
    """The composite type of two compatible declarations, 6.2.7p3.

    `int f(int[]);` and `int f(int *);` are the same function, and a later
    `int a[4];` after `extern int a[];` gives the array its size. Taking the
    more-specific of the two is how a redeclaration adds information rather
    than merely being checked against.
    """
    if a.kind is K.ARRAY and b.kind is K.ARRAY:
        count = a.count if a.count is not None else b.count
        return replace(a, count=count, of=composite(a.of, b.of))
    if a.kind is K.FUNCTION and b.kind is K.FUNCTION:
        params = a.params if a.params is not None else b.params
        variadic = a.variadic or b.variadic
        return function(composite(a.ret, b.ret), params, variadic)
    if a.kind is K.POINTER and b.kind is K.POINTER:
        return replace(a, of=composite(a.of, b.of))
    return a


# ── struct and union layout ─────────────────────────────────────────────────
def layout(tag: Tag) -> None:
    """Assign every member an offset, and the tag a size and an alignment.

    A BIT CURSOR OVER THE WHOLE STRUCT, not a byte cursor with a bit-field
    special case beside it. That is the System V rule, and the difference is
    visible in three lines of C:

        struct { char c; unsigned a : 12; char d; };

    The bit-field does not start a new byte: it begins at bit 8, inside the
    four-byte storage unit that already holds `c`, and `d` goes at byte 3.
    The whole struct is FOUR bytes. A layout that rounds up to the field's
    type before placing it makes the same struct twelve, and every offset in
    it disagrees with the platform -- which is not a performance question but
    a wrong answer, because a struct's layout is an ABI.

    A bit-field is placed at the cursor when it fits inside the storage unit
    of its declared type that the cursor is currently in, and at the start of
    the next one when it does not. A zero-width field names nothing and moves
    the cursor to the next unit boundary, which is the only thing it is for.
    """
    bit = 0                 # the cursor, in bits from the start
    align = 1
    union = tag.kind is K.UNION
    union_bits = 0

    for m in tag.members:
        if m.type.is_array and m.type.count is None and not m.type.is_vla:
            # A FLEXIBLE ARRAY MEMBER occupies no space and must be last; the
            # parser checks the ordering, because it has the span.
            m.offset = bit // 8
            m.bits = None
            tag.flexible = True
            continue
        malign = m.type.align
        if m.bits is not None:
            unit_bits = m.type.size * 8
            if m.bits == 0:
                bit = _round_up(bit, unit_bits)
                align = max(align, malign)
                continue
            if union:
                m.offset, m.bit_offset = 0, 0
                union_bits = max(union_bits, m.type.size * 8)
                align = max(align, malign)
                continue
            place = bit
            if (place % unit_bits) + m.bits > unit_bits:
                place = _round_up(place, unit_bits)
            m.offset = (place // unit_bits) * m.type.size
            m.bit_offset = place % unit_bits
            bit = place + m.bits
            align = max(align, malign)
            continue
        if union:
            m.offset = 0
            union_bits = max(union_bits, m.type.size * 8)
        else:
            bit = _round_up(bit, malign * 8)
            m.offset = bit // 8
            bit += m.type.size * 8
        align = max(align, malign)

    size = ((union_bits if union else bit) + 7) // 8
    tag.align = align
    tag.size = _round_up(size, align)
    # A STRUCT IS NEVER ZERO BYTES. `struct empty {};` is a C23 feature and a
    # GNU extension before it; both give it size 0, and an array of them would
    # then have every element at the same address. One byte is what C++ does
    # and what every C compiler that accepts the construct does.
    if tag.size == 0:
        tag.size = 1
        tag.align = max(tag.align, 1)
    tag.complete = True


def _round_up(value: int, align: int) -> int:
    return (value + align - 1) // align * align if align > 1 else value


def find_member(ty: CType, name: str) -> list[Member] | None:
    """The path to a member, through any anonymous struct or union members.

    A LIST, not a member: `u.x` where `x` lives in an anonymous struct inside
    `u` needs BOTH offsets to reach it, and returning only the inner member
    loses the outer offset. Breadth-first, so a member of the type itself
    always beats one nested inside an anonymous member.
    """
    if ty.tag is None or not ty.tag.complete:
        return None
    for m in ty.tag.members:
        if m.name == name:
            return [m]
    for m in ty.tag.members:
        if m.name is None and m.type.is_record:
            inner = find_member(m.type, name)
            if inner is not None:
                return [m] + inner
    return None


# ── spelling a type, for diagnostics ────────────────────────────────────────
def spell(ty: CType, inner: str = "") -> str:
    """A type as C would write it, with `inner` as the declarator.

    `spell(t)` alone gives `int (*)(char *, ...)`; `spell(t, "f")` gives
    `int (*f)(char *, ...)`. The parenthesising rule is the declarator
    grammar's, read backwards: a pointer to a function or an array needs
    parentheses because `*` binds looser than `()` and `[]`.
    """
    if ty.alias and not inner:
        return ty.alias
    quals = " ".join(q for q in _QUALIFIERS if q in ty.qual)
    k = ty.kind
    if k is K.POINTER:
        star = "*" + (quals + " " if quals else "")
        return spell(ty.of, f"({star}{inner})"
                     if ty.of.kind in (K.FUNCTION, K.ARRAY)
                     else f"{star}{inner}")
    if k is K.ARRAY:
        size = "" if ty.count is None else str(ty.count)
        if ty.vla is not None:
            size = "*"
        return spell(ty.of, f"{inner}[{size}]")
    if k is K.FUNCTION:
        if ty.params is None:
            args = ""
        elif not ty.params:
            args = "void"
        else:
            args = ", ".join(spell(p.type) for p in ty.params)
            if ty.variadic:
                args += ", ..."
        return spell(ty.ret, f"{inner}({args})")
    if k in (K.STRUCT, K.UNION, K.ENUM):
        name = ty.tag.name if ty.tag and ty.tag.name else "(anonymous)"
        base = f"{k.value} {name}"
    else:
        base = k.value
    if quals:
        base = f"{quals} {base}"
    return f"{base} {inner}".rstrip() if inner else base
