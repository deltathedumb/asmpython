"""Constant expressions.

REQUIRED, NOT OPTIONAL. An array bound, an enumeration value, a case label, a
bit-field width, a `_Static_assert` and every initialiser of an object with
static storage duration must be constants -- so this is part of type checking,
and its failure is a diagnostic about the program rather than a missed
optimisation. `passes/constfold.py` exists and is a different thing entirely:
it runs on IR, after this has already decided what the program means.

THREE KINDS OF CONSTANT, and C distinguishes them:

    integer constant expression   an array bound, a case label, `#if`
    arithmetic constant           an initialiser for a `double`
    address constant              `&x`, `"abc"`, `(char *)0 + 3`, and
                                  `&s.member` -- a symbol plus an offset,
                                  resolvable by the linker and not before

The third is why this returns a small union rather than a number. `static char
*p = "hi";` has no value until the string has an address, and a folder that can
only produce integers has to refuse it -- which would refuse most of the static
data in any real program.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import ctype as C
from . import syntax as S

_MASKS = {8: 0xFF, 16: 0xFFFF, 32: 0xFFFFFFFF, 64: (1 << 64) - 1}


@dataclass(frozen=True, slots=True)
class Address:
    """A symbol plus a byte offset. The linker resolves the symbol."""

    symbol: str
    offset: int = 0
    #: True when the symbol names a function rather than an object.
    function: bool = False


#: What `fold` returns. `None` means "not a constant", which is an answer and
#: not a failure -- `int a[n]` asks, gets None, and becomes a VLA.
Const = int | float | Address | None


def wrap(value: int, ty: C.CType) -> int:
    """Truncate to the type, honouring signedness. Integers WRAP in C, and a
    constant that wrapped at compile time must wrap the same way at run time
    or the two disagree about a case label."""
    bits = ty.bits
    mask = _MASKS.get(bits, (1 << bits) - 1)
    v = value & mask
    if ty.signed and v > mask >> 1:
        v -= mask + 1
    return v


def fold(e: S.Expr) -> Const:
    """The constant value of `e`, or None if it has none."""
    match e:
        case S.IntLit():
            return e.value
        case S.FloatLit():
            return e.value
        case S.StringLit():
            # A string literal IS an address constant -- of the array it
            # names. The symbol is assigned by lowering; until then the name
            # is None and the caller keeps the node instead.
            return Address(e.symbol) if e.symbol else None
        case S.SizeofType():
            return None if e.dynamic is not None else _sizeof(e)
        case S.Conv() | S.Cast():
            return _convert(fold(e.operand), e.operand.type, e.type)
        case S.Unary():
            return _unary(e)
        case S.Binary():
            return _binary(e)
        case S.Logical():
            return _logical(e)
        case S.Conditional():
            cond = fold(e.cond)
            if cond is None or isinstance(cond, Address):
                return None
            return fold(e.then if cond else e.otherwise)
        case S.Comma():
            # A comma is not permitted in a constant expression, and the
            # parser has already said so; folding the right side keeps the
            # recovery path from producing a second complaint.
            return fold(e.right)
        case S.Ident():
            sym = e.sym
            if sym is not None and sym.storage.name == "ENUM_CONST":
                return sym.value
            if sym is not None and sym.type.is_function:
                # A FUNCTION DESIGNATOR IS AN ADDRESS CONSTANT once it has
                # decayed, which is what makes `static Entry t[] = {{ add }}`
                # a static initialiser rather than something needing code.
                return Address(sym.ir_name or sym.name, function=True)
            return None
        case S.MemberAccess():
            base = fold(e.base) if e.arrow else _address_of_fold(e.base)
            if not isinstance(base, Address):
                return None
            return Address(base.symbol, base.offset + _path_offset(e.path))
        case S.Index():
            base = fold(e.base)
            index = fold(e.index)
            if not isinstance(base, Address) or not isinstance(index, int) \
                    or not isinstance(e.scale, int):
                return None
            return Address(base.symbol, base.offset + index * e.scale)
        case S.BuiltinCall():
            return _builtin(e)
    return None


def fold_int(e: S.Expr) -> int | None:
    """An INTEGER constant expression, or None. Rejects addresses and floats."""
    got = fold(e)
    if isinstance(got, bool):
        return int(got)
    return got if isinstance(got, int) else None


def _sizeof(e: S.SizeofType) -> Const:
    try:
        return e.operand_type.align if e.alignment else e.operand_type.size
    except C.IncompleteType:
        return None


def _address_of_fold(e: S.Expr) -> Const:
    """The address of an lvalue, for `&x` and for the base of `.`."""
    match e:
        case S.Ident():
            sym = e.sym
            if sym is None or not (sym.is_global or sym.storage.name == "TYPEDEF"):
                return None
            return Address(sym.ir_name or sym.name,
                           function=sym.type.is_function)
        case S.StringLit():
            return Address(e.symbol) if e.symbol else None
        case S.MemberAccess():
            base = fold(e.base) if e.arrow else _address_of_fold(e.base)
            if not isinstance(base, Address):
                return None
            return Address(base.symbol, base.offset + _path_offset(e.path))
        case S.Index():
            base = fold(e.base)
            index = fold(e.index)
            if not isinstance(base, Address) or not isinstance(index, int):
                return None
            return Address(base.symbol, base.offset + index * e.scale)
        case S.Unary() if e.op == "*":
            return fold(e.operand)
        case S.Conv() | S.Cast():
            return _address_of_fold(e.operand)
    return None


def _path_offset(path) -> int:
    return sum(m.offset for m in path)


def _convert(value: Const, source: C.CType, target: C.CType) -> Const:
    if value is None:
        return None
    if isinstance(value, Address):
        # A CAST DOES NOT MOVE AN ADDRESS. `(long)&x` is still the address of
        # `x`; only arithmetic on it changes the offset. Narrowing it would
        # be a truncation nothing can represent, so that case gives up.
        return value if target.size >= 8 or target.is_pointer else None
    if target.is_bool:
        return 1 if value else 0
    if target.is_integer:
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return None         # undefined; leave it to run time
            value = int(value)      # C truncates toward zero, as int() does
        return wrap(value, target)
    if target.is_float:
        v = float(value)
        if target.kind is C.K.FLOAT:
            import struct
            v = struct.unpack("<f", struct.pack("<f", v))[0]
        return v
    if target.is_pointer and isinstance(value, int):
        return value
    return None


def _unary(e: S.Unary) -> Const:
    if e.op == "&":
        return _address_of_fold(e.operand)
    if e.op == "*":
        return None                 # a load is never a constant
    v = fold(e.operand)
    if v is None or isinstance(v, Address):
        return None
    if e.op == "-":
        return wrap(-v, e.type) if isinstance(v, int) else -v
    if e.op == "+":
        return v
    if e.op == "~":
        return wrap(~v, e.type) if isinstance(v, int) else None
    if e.op == "!":
        return 0 if v else 1
    return None


def _logical(e: S.Logical) -> Const:
    left = fold(e.left)
    if left is None:
        return None
    truth = _truth(left)
    if truth is None:
        return None
    # SHORT CIRCUIT, and for the same reason `#if` does it: `p && p->x` is a
    # constant `0` when `p` is, and evaluating the right side would fail on
    # an expression the program never reaches.
    if e.op == "&&" and not truth:
        return 0
    if e.op == "||" and truth:
        return 1
    right = fold(e.right)
    if right is None:
        return None
    rt = _truth(right)
    return None if rt is None else (1 if rt else 0)


def _truth(v: Const) -> bool | None:
    if isinstance(v, Address):
        # The address of an object is never null, so `&x ? a : b` is `a` --
        # but only when the symbol is a real definition, which this cannot
        # see from here. Left unfolded rather than assumed.
        return None
    return bool(v)


def _binary(e: S.Binary) -> Const:
    a, b = fold(e.left), fold(e.right)
    if a is None or b is None:
        return None
    op = e.op
    if isinstance(a, Address) or isinstance(b, Address):
        return _address_arith(op, a, b, e)
    if op in ("==", "!=", "<", ">", "<=", ">="):
        r = {"==": a == b, "!=": a != b, "<": a < b,
             ">": a > b, "<=": a <= b, ">=": a >= b}[op]
        return 1 if r else 0
    if e.type.is_float:
        try:
            v = {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                 "/": lambda: a / b}[op]()
        except (KeyError, ZeroDivisionError):
            return None
        return _convert(v, e.type, e.type)
    if not isinstance(a, int) or not isinstance(b, int):
        return None
    if op in ("/", "%"):
        if b == 0:
            return None             # undefined; reported where it is required
        q = abs(a) // abs(b)
        q = -q if (a < 0) != (b < 0) else q
        return wrap(q if op == "/" else a - q * b, e.type)
    if op in ("<<", ">>"):
        if b < 0 or b >= e.type.bits:
            return None
        return wrap(a << b if op == "<<" else a >> b, e.type)
    if not isinstance(e.scale, int):
        return None             # a variable-length stride is not a constant
    table = {"+": lambda: a + b * e.scale, "-": lambda: a - b * e.scale,
             "*": lambda: a * b, "&": lambda: a & b, "|": lambda: a | b,
             "^": lambda: a ^ b}
    if op == "-p":
        return (a - b) // e.scale
    if op not in table:
        return None
    return wrap(table[op](), e.type)


def _address_arith(op: str, a: Const, b: Const, e: S.Binary) -> Const:
    """`&x + 3` and `(char *)&s.m - (char *)&s`. The only two that fold."""
    if not isinstance(e.scale, int):
        return None
    if op == "+" and isinstance(a, Address) and isinstance(b, int):
        return Address(a.symbol, a.offset + b * e.scale, a.function)
    if op == "+" and isinstance(b, Address) and isinstance(a, int):
        return Address(b.symbol, b.offset + a * e.scale, b.function)
    if op == "-" and isinstance(a, Address) and isinstance(b, int):
        return Address(a.symbol, a.offset - b * e.scale, a.function)
    if op == "-p" and isinstance(a, Address) and isinstance(b, Address) \
            and a.symbol == b.symbol:
        return (a.offset - b.offset) // e.scale
    if op in ("==", "!=") and isinstance(a, Address) and isinstance(b, Address):
        if a.symbol == b.symbol:
            same = a.offset == b.offset
            return (1 if same else 0) if op == "==" else (0 if same else 1)
    return None


def _builtin(e: S.BuiltinCall) -> Const:
    name = e.name
    if name == "__builtin_offsetof":
        return e.args[0].value if isinstance(e.args[0], S.IntLit) else None
    if name == "__builtin_types_compatible_p":
        return 1 if C.compatible(e.types[0].unqualified(),
                                 e.types[1].unqualified()) else 0
    if name == "__builtin_constant_p":
        return 1 if fold(e.args[0]) is not None else 0
    if name == "__builtin_expect":
        return fold(e.args[0])
    if name in ("__builtin_inf", "__builtin_huge_val"):
        return float("inf")
    if name in ("__builtin_inff", "__builtin_huge_valf"):
        return float("inf")
    if name in ("__builtin_nan", "__builtin_nanf"):
        return float("nan")
    if name in ("__builtin_fabs", "__builtin_fabsf"):
        v = fold(e.args[0])
        return abs(v) if isinstance(v, (int, float)) else None
    if name.startswith("__builtin_bswap"):
        v = fold(e.args[0])
        if not isinstance(v, int):
            return None
        width = int(name[len("__builtin_bswap"):]) // 8
        return int.from_bytes((v & ((1 << (width * 8)) - 1))
                              .to_bytes(width, "little"), "big")
    if name in ("__builtin_popcount", "__builtin_popcountl",
                "__builtin_popcountll"):
        v = fold(e.args[0])
        return bin(v & ((1 << 64) - 1)).count("1") if isinstance(v, int) else None
    return None
