"""Type checking for `__builtin_*`.

THREE OF THEM TAKE A TYPE, which no C function can, so they cannot be parsed
as ordinary calls: `__builtin_va_arg(ap, int)` would try to read `int` as an
expression and report that a type is not a value. `parse_type_builtin` is
called from `postfix` BEFORE the argument list is parsed, for exactly the names
that need it.

The rest are ordinary calls with unusual signatures, and `check_builtin` gives
each one the checking a prototype would have given it -- so
`__builtin_popcount(3.5)` is a diagnostic about an argument rather than a
surprise in lowering.
"""
from __future__ import annotations

from ...diagnostics import Span
from . import ctype as C
from . import syntax as S
from .ctype import CType
from .tokens import Kind

#: The builtins whose arguments are not all expressions.
TYPE_BUILTINS = frozenset({
    "__builtin_va_arg", "__builtin_offsetof", "__builtin_types_compatible_p",
})

#: name -> (argument types, result). `None` in the argument list means "any
#: scalar"; a trailing `...` means the rest are unchecked.
_SIGNATURES: dict[str, tuple[tuple, CType]] = {
    "__builtin_clz": ((C.UINT,), C.INT),
    "__builtin_clzl": ((C.ULONG,), C.INT),
    "__builtin_clzll": ((C.ULLONG,), C.INT),
    "__builtin_ctz": ((C.UINT,), C.INT),
    "__builtin_ctzl": ((C.ULONG,), C.INT),
    "__builtin_ctzll": ((C.ULLONG,), C.INT),
    "__builtin_popcount": ((C.UINT,), C.INT),
    "__builtin_popcountl": ((C.ULONG,), C.INT),
    "__builtin_popcountll": ((C.ULLONG,), C.INT),
    "__builtin_bswap16": ((C.USHORT,), C.USHORT),
    "__builtin_bswap32": ((C.UINT,), C.UINT),
    "__builtin_bswap64": ((C.ULLONG,), C.ULLONG),
    "__builtin_memcpy": ((C.VOID_PTR, C.CONST_CHAR_PTR, C.SIZE_T), C.VOID_PTR),
    "__builtin_memmove": ((C.VOID_PTR, C.CONST_CHAR_PTR, C.SIZE_T), C.VOID_PTR),
    "__builtin_memset": ((C.VOID_PTR, C.INT, C.SIZE_T), C.VOID_PTR),
    "__builtin_memcmp": ((C.CONST_CHAR_PTR, C.CONST_CHAR_PTR, C.SIZE_T), C.INT),
    "__builtin_strlen": ((C.CONST_CHAR_PTR,), C.SIZE_T),
    "__builtin_alloca": ((C.SIZE_T,), C.VOID_PTR),
    "__builtin_nan": ((C.CONST_CHAR_PTR,), C.DOUBLE),
    "__builtin_nanf": ((C.CONST_CHAR_PTR,), C.FLOAT),
    "__builtin_inf": ((), C.DOUBLE),
    "__builtin_inff": ((), C.FLOAT),
    "__builtin_huge_val": ((), C.DOUBLE),
    "__builtin_huge_valf": ((), C.FLOAT),
    "__builtin_fabs": ((C.DOUBLE,), C.DOUBLE),
    "__builtin_fabsf": ((C.FLOAT,), C.FLOAT),
    "__builtin_sqrt": ((C.DOUBLE,), C.DOUBLE),
    "__builtin_sqrtf": ((C.FLOAT,), C.FLOAT),
    "__builtin_copysign": ((C.DOUBLE, C.DOUBLE), C.DOUBLE),
    "__builtin_isnan": ((C.DOUBLE,), C.INT),
    "__builtin_isinf": ((C.DOUBLE,), C.INT),
    "__builtin_isfinite": ((C.DOUBLE,), C.INT),
    "__builtin_signbit": ((C.DOUBLE,), C.INT),
    "__builtin_unreachable": ((), C.VOID),
    "__builtin_trap": ((), C.VOID),
    "__builtin_va_end": ((None,), C.VOID),
    "__builtin_va_copy": ((None, None), C.VOID),
}


def _need_address(p, e: S.Expr, name: str) -> bool:
    """A `va_list` argument is written through, so it needs a location.

    NOTHING IN THE SOURCE TAKES ITS ADDRESS. `va_arg(ap, int)` advances `ap`,
    and the only `&` involved is the one this builtin does on the programmer's
    behalf -- so without this the parser sees a `va_list` that is never
    addressed, lowering keeps it in a register, and the advance has nowhere to
    be stored. The symptom was the frontend refusing its own `<stdarg.h>`.
    """
    inner = e
    while isinstance(inner, (S.Conv, S.Cast)):
        inner = inner.operand
    if not inner.lvalue:
        p.sema.error("E1299", f"{name} needs a va_list it can modify",
                     inner.span,
                     note="it advances the list, so it must be an object")
        return False
    p.sema._mark_addressed(inner)
    return True


def parse_type_builtin(p, name: str, span: Span) -> S.Expr:
    """`__builtin_va_arg`, `__builtin_offsetof`, `__builtin_types_compatible_p`."""
    p.expect("(", f"{name} needs parentheses")
    if name == "__builtin_va_arg":
        ap = p.sema.lvalue_conversion(p.assignment())
        p.expect(",", "__builtin_va_arg takes a list and a type")
        ty = p.type_name()
        p.expect(")")
        if not ap.type.is_pointer:
            p.sema.error("E1290",
                         f"__builtin_va_arg needs a va_list, not "
                         f"{C.spell(ap.type)}", span)
            return p.sema.poison(span)
        if not _need_address(p, ap, "__builtin_va_arg"):
            return p.sema.poison(span)
        if ty.is_array or ty.is_function or not ty.complete:
            p.sema.error("E1291",
                         f"__builtin_va_arg cannot produce {C.spell(ty)}",
                         span)
            return p.sema.poison(span)
        return S.VaArg(span, ty.unqualified(), False, ap)
    if name == "__builtin_types_compatible_p":
        a = p.type_name()
        p.expect(",", "__builtin_types_compatible_p takes two types")
        b = p.type_name()
        p.expect(")")
        node = S.BuiltinCall(span, C.INT, False, name, [], [a, b])
        return node
    # __builtin_offsetof(T, member-designator)
    ty = p.type_name()
    p.expect(",", "__builtin_offsetof takes a type and a member")
    offset, ok = _designator_offset(p, ty, span)
    p.expect(")")
    if not ok:
        return p.sema.poison(span)
    return S.BuiltinCall(span, C.SIZE_T, False, name,
                         [S.IntLit(span, C.SIZE_T, False, offset)], [ty])


def _designator_offset(p, ty: CType, span: Span) -> tuple[int, bool]:
    """`a.b[2].c` -- the byte offset of a member, for `offsetof`."""
    total = 0
    cur = ty
    first = True
    while True:
        if first or p.at("."):
            if not first:
                p.next()
            name = p.tok
            if name.kind is not Kind.IDENT:
                p.sema.error("E1292", "expected a member name", name.span)
                return 0, False
            p.next()
            if not cur.is_record or not cur.complete:
                p.sema.error("E1293",
                             f"{C.spell(cur)} has no members", name.span)
                return 0, False
            path = C.find_member(cur, name.text)
            if path is None:
                p.sema.error("E1432",
                             f"{C.spell(cur)} has no member named "
                             f"{name.text!r}", name.span)
                return 0, False
            if path[-1].is_bitfield:
                p.sema.error("E1294",
                             f"offsetof of the bit-field {name.text!r}",
                             name.span,
                             note="a bit-field has no byte offset of its own")
                return 0, False
            total += sum(m.offset for m in path)
            cur = path[-1].type
            first = False
            continue
        if p.at("["):
            open_tok = p.next()
            index = p.conditional()
            p.expect("]")
            from .fold import fold_int
            got = fold_int(index)
            if got is None or not cur.is_array:
                p.sema.error("E1295",
                             "offsetof needs a constant subscript of an array",
                             open_tok.span)
                return 0, False
            total += got * cur.of.size
            cur = cur.of
            continue
        return total, True


def check_builtin(p, name: str, args: list[S.Expr], span: Span) -> S.Expr:
    """An ordinary `__builtin_*` call: check the arguments and build the node."""
    if name == "__builtin_va_start":
        # `va_start(ap, last)` in C23 may be called with one argument. The
        # second is not evaluated in either case -- the argument area's
        # address is a property of the frame, not of `last`.
        if not 1 <= len(args) <= 2:
            return _arity(p, name, "1 or 2", args, span)
        if not _need_address(p, args[0], name):
            return p.sema.poison(span)
        return S.BuiltinCall(span, C.VOID, False, name, args[:1], [])
    if name == "__builtin_expect":
        if len(args) != 2:
            return _arity(p, name, "2", args, span)
        value = p.sema.lvalue_conversion(args[0])
        return S.BuiltinCall(span, value.type, False, name, [value], [])
    if name == "__builtin_constant_p":
        if len(args) != 1:
            return _arity(p, name, "1", args, span)
        return S.BuiltinCall(span, C.INT, False, name, args, [])
    sig = _SIGNATURES.get(name)
    if sig is None:
        p.sema.error("E1296", f"{name} is not implemented", span,
                     note="it is listed in builtins.py but has no rule here")
        return p.sema.poison(span)
    want, result = sig
    if len(args) != len(want):
        return _arity(p, name, str(len(want)), args, span)
    if name == "__builtin_va_copy" and not _need_address(p, args[0], name):
        return p.sema.poison(span)
    checked: list[S.Expr] = []
    for i, (arg, target) in enumerate(zip(args, want)):
        if target is None:
            got = p.sema.lvalue_conversion(arg)
            if not got.type.is_scalar:
                p.sema.error("E1297",
                             f"argument {i + 1} of {name} must be a scalar, "
                             f"not {C.spell(got.type)}", arg.span)
                return p.sema.poison(span)
            checked.append(got)
            continue
        converted = p.sema.assignable(target, arg, f"argument {i + 1} of {name}",
                                      arg.span)
        checked.append(converted if converted is not None else arg)
    return S.BuiltinCall(span, result, False, name, checked, [])


def _arity(p, name: str, want: str, args: list[S.Expr], span: Span) -> S.Expr:
    p.sema.error("E1298",
                 f"{name} takes {want} argument(s), {len(args)} given", span)
    return p.sema.poison(span)
