"""Code for the `__builtin_*` names.

Each one is here because C has no way to write it, or because writing it in C
would be slower than the single instruction every machine has. They are not a
library: nothing links against them, there is no `__builtin_popcount` symbol,
and a program that calls one gets the instructions inline.

`__builtin_va_start` AND `__builtin_va_arg` are the two that a C library cannot
do without: `<stdarg.h>` is the one standard header whose contents are not
writable in C, and this is why.
"""
from __future__ import annotations

from ...diagnostics import error
from ...ir import types as IR
from ...ir.module import Instruction
from ...ir.opcodes import Op
from . import ctype as C
from . import syntax as S


def lower_builtin(L, e: S.BuiltinCall) -> int:
    """Emit `e`. `L` is the Lowerer; this is a method in all but spelling."""
    name = e.name
    b = L.b
    if name == "__builtin_va_start":
        if L.va_area is None:
            L.sink.report(
                error("E1502", "va_start in a function that is not variadic")
                .at(e.span))
            return b.const(IR.I64, 0)
        addr = L._address(_bare(e.args[0]))
        b.store(IR.PTR, L.va_area, addr)
        return b.const(IR.I64, 0)
    if name == "__builtin_va_end":
        return b.const(IR.I64, 0)
    if name == "__builtin_va_copy":
        dst = L._address(_bare(e.args[0]))
        src = L._value(e.args[1])
        b.store(IR.PTR, src, dst)
        return b.const(IR.I64, 0)

    if name in ("__builtin_offsetof", "__builtin_types_compatible_p",
                "__builtin_constant_p"):
        from .fold import fold
        return b.const(C.to_ir(e.type), fold(e) or 0)
    if name == "__builtin_expect":
        return L._value(e.args[0])
    if name == "__builtin_unreachable":
        b.emit(Instruction(Op.UNREACHABLE, IR.VOID))
        L._open(b.new_block("dead"))
        return b.const(IR.I64, 0)
    if name == "__builtin_trap":
        b.emit(Instruction(Op.UNREACHABLE, IR.VOID))
        L._open(b.new_block("dead"))
        return b.const(IR.I64, 0)
    if name == "__builtin_alloca":
        L.needs_vla = True
        size = L._ir_convert(L._value(e.args[0]), e.args[0].type, IR.I64)
        return b.call(IR.PTR, "__c_vla_alloc", [size])

    if name in ("__builtin_inf", "__builtin_huge_val"):
        return b.const(IR.F64, float("inf"))
    if name in ("__builtin_inff", "__builtin_huge_valf"):
        return b.const(IR.F32, float("inf"))
    if name in ("__builtin_nan", "__builtin_nanf"):
        L._discard(e.args[0])
        return b.const(IR.F64 if name == "__builtin_nan" else IR.F32,
                       float("nan"))

    if name in ("__builtin_fabs", "__builtin_fabsf"):
        ir = IR.F64 if name == "__builtin_fabs" else IR.F32
        return _fabs(L, L._value(e.args[0]), ir)
    if name == "__builtin_copysign":
        return _copysign(L, L._value(e.args[0]), L._value(e.args[1]))
    if name in ("__builtin_sqrt", "__builtin_sqrtf"):
        # THE LIBRARY'S OWN `sqrt`, not a machine instruction: the IR has no
        # square-root opcode, and `<math.h>`'s implementation is compiled from
        # C by this same frontend. The builtin exists so a header can call it
        # without `<math.h>` being included, not to be faster.
        ir = IR.F64 if name == "__builtin_sqrt" else IR.F32
        value = L._value(e.args[0])
        L._ensure_extern("sqrt", IR.F64, [IR.F64])
        got = b.call(IR.F64, "sqrt", [L._ir_convert(value, e.args[0].type,
                                                    IR.F64)])
        return L._ir_convert(got, C.DOUBLE, ir)
    if name in ("__builtin_isnan", "__builtin_isinf", "__builtin_isfinite",
                "__builtin_signbit"):
        return _classify(L, name, L._value(e.args[0]))

    if name.startswith("__builtin_bswap"):
        width = int(name[len("__builtin_bswap"):])
        return _bswap(L, L._value(e.args[0]), width)
    if name.startswith(("__builtin_clz", "__builtin_ctz", "__builtin_popcount")):
        return _bitcount(L, name, e)

    if name in ("__builtin_memcpy", "__builtin_memmove", "__builtin_memset",
                "__builtin_memcmp", "__builtin_strlen"):
        return _mem(L, name, e)
    raise AssertionError(f"no lowering for {name}")


def _bare(e: S.Expr) -> S.Expr:
    while isinstance(e, (S.Conv, S.Cast)):
        e = e.operand
    return e


def _fabs(L, value: int, ir: IR.Type) -> int:
    """Clear the sign bit. A compare-and-negate would give `-0.0` for `0.0`
    and NaN for NaN with the sign still on; masking the bits is what the
    instruction every machine has does."""
    ity = IR.U64 if ir is IR.F64 else IR.U32
    bits = L._emit_conv(Op.BITCAST, ity, value)
    mask = L.b.const(ity, (1 << (ity.bits - 1)) - 1)
    d = L.b.reg(ity)
    L.b.emit(Instruction(Op.AND, ity, dst=d, args=[bits, mask]))
    return L._emit_conv(Op.BITCAST, ir, d)


def _copysign(L, magnitude: int, sign: int) -> int:
    ity = IR.U64
    top = 1 << 63
    m = L._emit_conv(Op.BITCAST, ity, magnitude)
    s = L._emit_conv(Op.BITCAST, ity, sign)
    body = L.b.reg(ity)
    L.b.emit(Instruction(Op.AND, ity, dst=body,
                         args=[m, L.b.const(ity, top - 1)]))
    signbit = L.b.reg(ity)
    L.b.emit(Instruction(Op.AND, ity, dst=signbit,
                         args=[s, L.b.const(ity, top)]))
    merged = L.b.reg(ity)
    L.b.emit(Instruction(Op.OR, ity, dst=merged, args=[body, signbit]))
    return L._emit_conv(Op.BITCAST, IR.F64, merged)


def _classify(L, name: str, value: int) -> int:
    """`isnan`, `isinf`, `isfinite` and `signbit`, on the bits.

    NOT ON THE VALUE. `x != x` is the classic NaN test and it is correct, but
    `isinf` written as `x == INFINITY || x == -INFINITY` needs two constants
    and two compares where one mask and one compare will do -- and `signbit`
    has no arithmetic spelling at all, because `-0.0 < 0` is false.
    """
    b = L.b
    bits = L._emit_conv(Op.BITCAST, IR.U64, value)
    if name == "__builtin_signbit":
        shifted = b.reg(IR.U64)
        b.emit(Instruction(Op.SHR, IR.U64, dst=shifted,
                           args=[bits, b.const(IR.U64, 63)]))
        return L._int_convert(shifted, IR.U64, IR.I32)
    absolute = b.reg(IR.U64)
    b.emit(Instruction(Op.AND, IR.U64, dst=absolute,
                       args=[bits, b.const(IR.U64, (1 << 63) - 1)]))
    inf = b.const(IR.U64, 0x7FF0000000000000)
    if name == "__builtin_isnan":
        got = b.cmp(Op.GT, IR.U64, absolute, inf)
    elif name == "__builtin_isinf":
        got = b.cmp(Op.EQ, IR.U64, absolute, inf)
    else:
        got = b.cmp(Op.LT, IR.U64, absolute, inf)
    return L._int_convert(got, IR.I1, IR.I32)


def _bswap(L, value: int, width: int) -> int:
    ir = {16: IR.U16, 32: IR.U32, 64: IR.U64}[width]
    b = L.b
    value = L._ir_convert(value, L.fn.register_type(value), ir)
    out = None
    for i in range(width // 8):
        shift = i * 8
        byte = value
        if shift:
            byte = b.reg(ir)
            b.emit(Instruction(Op.SHR, ir, dst=byte,
                               args=[value, b.const(ir, shift)]))
        masked = b.reg(ir)
        b.emit(Instruction(Op.AND, ir, dst=masked,
                           args=[byte, b.const(ir, 0xFF)]))
        place = width - 8 - shift
        if place:
            moved = b.reg(ir)
            b.emit(Instruction(Op.SHL, ir, dst=moved,
                               args=[masked, b.const(ir, place)]))
            masked = moved
        if out is None:
            out = masked
        else:
            merged = b.reg(ir)
            b.emit(Instruction(Op.OR, ir, dst=merged, args=[out, masked]))
            out = merged
    return out


def _bitcount(L, name: str, e: S.BuiltinCall) -> int:
    """`clz`, `ctz` and `popcount`, as a loop.

    A LOOP AND NOT A TABLE. The IR has no count-leading-zeros opcode, so
    something has to spell it out; a de Bruijn table needs a global and a
    multiply, and a loop of at most 64 iterations is what a backend can turn
    back into one instruction if it ever learns to. Correctness first, and
    the shape is recognisable.
    """
    b = L.b
    kind = ("clz" if "clz" in name else
            "ctz" if "ctz" in name else "popcount")
    # `__builtin_clz` counts an `unsigned int`; the `l` and `ll` spellings
    # count a `long` and a `long long`, and both are 64 bits here. The suffix
    # is what says which, so it is read rather than guessed from the argument.
    width = 64 if name.endswith(("l", "ll")) else 32
    ir = IR.U32 if width == 32 else IR.U64
    value = L._ir_convert(L._value(e.args[0]), e.args[0].type, ir)
    counter = b.reg(IR.I32)
    b.copy(counter, b.const(IR.I32, 0))
    cursor = b.reg(ir)
    b.copy(cursor, value)
    head = b.new_block("bits")
    body = b.new_block("bitsbody")
    after = b.new_block("bitsend")
    b.jump(head)
    L._open(head)
    if kind == "popcount":
        test = b.cmp(Op.NE, ir, cursor, b.const(ir, 0))
        b.branch(test, body, after)
        L._open(body)
        low = b.reg(ir)
        b.emit(Instruction(Op.AND, ir, dst=low, args=[cursor, b.const(ir, 1)]))
        add = b.cmp(Op.NE, ir, low, b.const(ir, 0))
        widened = L._int_convert(add, IR.I1, IR.I32)
        b.copy(counter, b.add(IR.I32, counter, widened))
        shifted = b.reg(ir)
        b.emit(Instruction(Op.SHR, ir, dst=shifted,
                           args=[cursor, b.const(ir, 1)]))
        b.copy(cursor, shifted)
        b.jump(head)
        L._open(after)
        return counter
    limit = b.const(IR.I32, width)
    more = b.cmp(Op.LT, IR.I32, counter, limit)
    b.branch(more, body, after)
    L._open(body)
    if kind == "clz":
        probe = b.reg(ir)
        b.emit(Instruction(Op.SHR, ir, dst=probe,
                           args=[cursor, b.const(ir, width - 1)]))
    else:
        probe = b.reg(ir)
        b.emit(Instruction(Op.AND, ir, dst=probe, args=[cursor, b.const(ir, 1)]))
    hit = b.cmp(Op.NE, ir, probe, b.const(ir, 0))
    step = b.new_block("bitsstep")
    b.branch(hit, after, step)
    L._open(step)
    b.copy(counter, b.add(IR.I32, counter, b.const(IR.I32, 1)))
    moved = b.reg(ir)
    b.emit(Instruction(Op.SHL if kind == "clz" else Op.SHR, ir, dst=moved,
                       args=[cursor, b.const(ir, 1)]))
    b.copy(cursor, moved)
    b.jump(head)
    L._open(after)
    return counter


def _mem(L, name: str, e: S.BuiltinCall) -> int:
    """`memcpy` and friends, when the size is a constant.

    A CONSTANT SIZE BECOMES A RUN OF LOADS AND STORES, which is what makes
    `__builtin_memcpy(&a, &b, sizeof a)` as cheap as an assignment. Anything
    else calls the library's own function -- and that call is why
    `<string.h>` declares them: the builtin is an optimisation of a real
    function, not a replacement for one.
    """
    from .fold import fold
    b = L.b
    args = [L._value(a) for a in e.args]
    if name == "__builtin_strlen":
        L._ensure_extern("strlen", IR.U64, [IR.PTR])
        return b.call(IR.U64, "strlen", args)
    size = fold(e.args[2]) if len(e.args) > 2 else None
    if isinstance(size, int) and size <= 128:
        if name in ("__builtin_memcpy", "__builtin_memmove"):
            L._copy(args[0], args[1], size, 1)
            return args[0]
        if name == "__builtin_memset":
            value = fold(e.args[1])
            if value == 0:
                L._zero(args[0], size)
                return args[0]
    plain = name[len("__builtin_"):]
    ret = IR.I32 if plain == "memcmp" else IR.PTR
    L._ensure_extern(plain, ret, [L.fn.register_type(a) for a in args])
    return b.call(ret, plain, args)
