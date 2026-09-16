"""The `__builtin_*` names this frontend answers for.

A C program may not mention any of these; a C HEADER almost certainly does.
`<stdarg.h>` cannot be written in C at all -- `va_start` has to know where the
frame's argument area is -- and `assert`, `offsetof` and the byte-swap and
count-leading-zero idioms are all spelled as builtins in every real toolchain.

WHAT IS HERE IS WHAT LOWERING IMPLEMENTS. `__has_builtin(x)` reads this set and
nothing else, so a header that guards on it gets an answer that matches what
actually happens when it calls the thing -- rather than the usual arrangement
where the guard says yes and the link fails.
"""
from __future__ import annotations

#: name -> a short description, printed by `uasm frontends --verbose` and
#: used by nothing else. The SET is the contract; the text is for humans.
BUILTINS: dict[str, str] = {
    # ── varargs. See lower.py: a variadic function gets one extra parameter,
    # a pointer to its argument area, and these three walk it.
    "__builtin_va_start": "begin traversing a variadic argument list",
    "__builtin_va_arg": "the next variadic argument, of a named type",
    "__builtin_va_end": "finish traversing a variadic argument list",
    "__builtin_va_copy": "duplicate a traversal in progress",

    # ── the ones a header uses to describe the program to itself ───────────
    "__builtin_offsetof": "byte offset of a member within a type",
    "__builtin_types_compatible_p": "whether two types are the same type",
    "__builtin_constant_p": "whether an expression folded to a constant",
    "__builtin_expect": "a branch hint; the value is its first argument",
    "__builtin_unreachable": "control never arrives here",
    "__builtin_trap": "stop the program abnormally",

    # ── memory, because <string.h>'s own definitions call them ─────────────
    "__builtin_memcpy": "copy bytes between non-overlapping objects",
    "__builtin_memmove": "copy bytes, overlapping permitted",
    "__builtin_memset": "fill bytes with a value",
    "__builtin_memcmp": "compare bytes",
    "__builtin_strlen": "length of a null-terminated string",
    "__builtin_alloca": "frame storage that lives until the function returns",

    # ── bit counting. One instruction on every machine and a loop in C. ────
    "__builtin_clz": "count leading zero bits of an unsigned int",
    "__builtin_clzl": "count leading zero bits of an unsigned long",
    "__builtin_clzll": "count leading zero bits of an unsigned long long",
    "__builtin_ctz": "count trailing zero bits of an unsigned int",
    "__builtin_ctzl": "count trailing zero bits of an unsigned long",
    "__builtin_ctzll": "count trailing zero bits of an unsigned long long",
    "__builtin_popcount": "count set bits of an unsigned int",
    "__builtin_popcountl": "count set bits of an unsigned long",
    "__builtin_popcountll": "count set bits of an unsigned long long",
    "__builtin_bswap16": "reverse the bytes of a 16-bit value",
    "__builtin_bswap32": "reverse the bytes of a 32-bit value",
    "__builtin_bswap64": "reverse the bytes of a 64-bit value",

    # ── floating point, where the operation has no C spelling ─────────────
    "__builtin_nan": "a quiet NaN",
    "__builtin_nanf": "a quiet NaN, float",
    "__builtin_inf": "positive infinity",
    "__builtin_inff": "positive infinity, float",
    "__builtin_huge_val": "HUGE_VAL",
    "__builtin_huge_valf": "HUGE_VALF",
    "__builtin_fabs": "absolute value, double",
    "__builtin_fabsf": "absolute value, float",
    "__builtin_isnan": "whether a floating value is NaN",
    "__builtin_isinf": "whether a floating value is an infinity",
    "__builtin_isfinite": "whether a floating value is neither NaN nor inf",
    "__builtin_signbit": "the sign bit of a floating value",
    "__builtin_copysign": "a magnitude with another value's sign",
    "__builtin_sqrt": "square root, double",
    "__builtin_sqrtf": "square root, float",
}

#: `__func__` is not a builtin -- it is a predefined identifier, 6.4.2.2, and
#: it behaves like `static const char __func__[] = "name";` at the top of every
#: function body. Named here because that is where a reader looks for it.
FUNCTION_NAME_IDENTIFIERS = ("__func__", "__FUNCTION__", "__PRETTY_FUNCTION__")
