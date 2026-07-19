"""math module: binds to libm. All trig/exp functions take and return float."""
from __future__ import annotations

from . import Func, Const


# Constant values are written as literals (not `math.pi` from CPython's math
# module): the binding table is pure, self-contained data so it can be merged
# into a whole-program compile without pulling in a CPython-runtime dependency.
# These are the exact IEEE-754 double constants CPython uses.
BINDINGS = {
    # Constants
    "pi":  Const(ty="float", value=3.141592653589793),
    "e":   Const(ty="float", value=2.718281828459045),
    "tau": Const(ty="float", value=6.283185307179586),
    "inf": Const(ty="float", value=float("inf")),
    "nan": Const(ty="float", value=float("nan")),

    # Single-argument float-> float
    "sqrt":  Func(arg_types=("float",), ret_type="float", c_name="sqrt"),
    "cbrt":  Func(arg_types=("float",), ret_type="float", c_name="cbrt"),
    "exp":   Func(arg_types=("float",), ret_type="float", c_name="exp"),
    "log":   Func(arg_types=("float",), ret_type="float", c_name="log"),
    "log2":  Func(arg_types=("float",), ret_type="float", c_name="log2"),
    "log10": Func(arg_types=("float",), ret_type="float", c_name="log10"),
    "sin":   Func(arg_types=("float",), ret_type="float", c_name="sin"),
    "cos":   Func(arg_types=("float",), ret_type="float", c_name="cos"),
    "tan":   Func(arg_types=("float",), ret_type="float", c_name="tan"),
    "asin":  Func(arg_types=("float",), ret_type="float", c_name="asin"),
    "acos":  Func(arg_types=("float",), ret_type="float", c_name="acos"),
    "atan":  Func(arg_types=("float",), ret_type="float", c_name="atan"),
    "sinh":  Func(arg_types=("float",), ret_type="float", c_name="sinh"),
    "cosh":  Func(arg_types=("float",), ret_type="float", c_name="cosh"),
    "tanh":  Func(arg_types=("float",), ret_type="float", c_name="tanh"),
    # CPython's math.floor/ceil/trunc return int (not the libm double).
    "floor": Func(arg_types=("float",), ret_type="int", c_name="floor", ret_conv="f2i"),
    "ceil":  Func(arg_types=("float",), ret_type="int", c_name="ceil", ret_conv="f2i"),
    "trunc": Func(arg_types=("float",), ret_type="int", c_name="trunc", ret_conv="f2i"),
    "fabs":  Func(arg_types=("float",), ret_type="float", c_name="fabs"),
    # Inverse hyperbolics and exp/log variants (C99 libm; present in msvcrt/ucrt).
    "asinh": Func(arg_types=("float",), ret_type="float", c_name="asinh"),
    "acosh": Func(arg_types=("float",), ret_type="float", c_name="acosh"),
    "atanh": Func(arg_types=("float",), ret_type="float", c_name="atanh"),
    "exp2":  Func(arg_types=("float",), ret_type="float", c_name="exp2"),
    "expm1": Func(arg_types=("float",), ret_type="float", c_name="expm1"),
    "log1p": Func(arg_types=("float",), ret_type="float", c_name="log1p"),
    # Rounding to a float-valued integer (CPython's round() is the builtin; this
    # is the libm nearbyint, handy in numeric code).
    "nearbyint": Func(arg_types=("float",), ret_type="float", c_name="nearbyint"),

    # Two-argument float, float -> float
    "pow":   Func(arg_types=("float", "float"), ret_type="float", c_name="pow"),
    "atan2": Func(arg_types=("float", "float"), ret_type="float", c_name="atan2"),
    "hypot": Func(arg_types=("float", "float"), ret_type="float", c_name="hypot"),
    "fmod":  Func(arg_types=("float", "float"), ret_type="float", c_name="fmod"),
    "copysign":  Func(arg_types=("float", "float"), ret_type="float", c_name="copysign"),
    "remainder": Func(arg_types=("float", "float"), ret_type="float", c_name="remainder"),
    "fdim":      Func(arg_types=("float", "float"), ret_type="float", c_name="fdim"),
    "fmax":      Func(arg_types=("float", "float"), ret_type="float", c_name="fmax"),
    "fmin":      Func(arg_types=("float", "float"), ret_type="float", c_name="fmin"),

    # Classification predicates (return int 0/1 like CPython's bool)
    # isnan(x) -> bool: True if x is NaN
    "isnan":    Func(arg_types=("float",), ret_type="int", c_name="_math_isnan"),
    # isinf(x) -> bool: True if x is +inf or -inf
    "isinf":    Func(arg_types=("float",), ret_type="int", c_name="_math_isinf"),
    # isfinite(x) -> bool: True if x is finite (not nan, not inf)
    "isfinite": Func(arg_types=("float",), ret_type="int", c_name="_math_isfinite"),

    # Angle conversion
    # degrees(x: float) -> float: radians to degrees
    "degrees":  Func(arg_types=("float",), ret_type="float", c_name="_math_degrees"),
    # radians(x: float) -> float: degrees to radians
    "radians":  Func(arg_types=("float",), ret_type="float", c_name="_math_radians"),

    # GCD / LCM (integer inputs, CPython 3.5+)
    # gcd(a, b) -> int
    "gcd":      Func(arg_types=("int", "int"), ret_type="int", c_name="_math_gcd"),
    # lcm(a, b) -> int (CPython 3.9+)
    "lcm":      Func(arg_types=("int", "int"), ret_type="int", c_name="_math_lcm"),

    # Combinatorics (CPython 3.8+)
    # factorial(n) -> int
    "factorial": Func(arg_types=("int",), ret_type="int", c_name="_math_factorial"),
    # comb(n, k) -> int  (binomial coefficient, CPython 3.8+)
    "comb":     Func(arg_types=("int", "int"), ret_type="int", c_name="_math_comb"),
    # perm(n, k) -> int  (permutations, CPython 3.8+)
    "perm":     Func(arg_types=("int", "int"), ret_type="int", c_name="_math_perm"),

    # log(x, base) — two-arg form: log(x) / log(base) (CPython math.log(x, base))
    "log_base": Func(arg_types=("float", "float"), ret_type="float", c_name="_math_log_base"),

    # modf(x) -> (frac, int_part)  — returns fractional part; int part via out-param
    # Exposed as modf_frac / modf_int since asmpython can't return tuples from FFI.
    "modf_frac": Func(arg_types=("float",), ret_type="float", c_name="_math_modf_frac"),
    "modf_int":  Func(arg_types=("float",), ret_type="float", c_name="_math_modf_int"),

    # frexp(x) -> (mantissa, exponent): split float into [0.5,1) mantissa * 2^exp
    "frexp_mantissa": Func(arg_types=("float",), ret_type="float", c_name="_math_frexp_m"),
    "frexp_exponent": Func(arg_types=("float",), ret_type="int",   c_name="_math_frexp_e"),

    # ldexp(x, i) -> x * 2**i  (inline helper avoids mixed float/int ABI issues)
    "ldexp":    Func(arg_types=("float", "int"), ret_type="float", c_name="_math_ldexp"),

    # isqrt(n) -> int: floor(sqrt(n)) for non-negative integers
    "isqrt":    Func(arg_types=("int",),  ret_type="int",   c_name="_math_isqrt"),

    # isclose(a, b, rel_tol, abs_tol) -> bool: True if a ≈ b within tolerances
    "isclose":  Func(arg_types=("float", "float", "float", "float"), ret_type="int", c_name="_math_isclose"),

    # erf / erfc (error function, C99). Neither is a real msvcrt.dll export
    # (confirmed via ctypes.WinDLL('msvcrt.dll') attribute lookup). The
    # legacy gcc-linked backend still resolves `erf` fine (mingw bundles a
    # static libm with it), so `c_name` stays the plain C symbol here --
    # the x86-64 backend's own from-scratch PE linker has no such library
    # to draw from, so ir_lower.py's call-lowering special-cases c_name ==
    # "erf" to route to a native computed shim (_math_erf in abi_shims.asm:
    # Abramowitz & Stegun 7.1.26 polynomial approximation) instead of an
    # unresolvable extern, mirroring how exp2/fmax/fmin are handled there.
    # erfc has no such special-case yet (nothing in-tree exercises it on
    # x86-64); add one (e.g. a `_math_erfc` shim == 1 - erf(x)) if needed.
    "erf":      Func(arg_types=("float",), ret_type="float", c_name="erf"),
    "erfc":     Func(arg_types=("float",), ret_type="float", c_name="erfc"),

    # tgamma / lgamma (gamma functions, C99). Same story as erf: tgamma
    # isn't a real msvcrt.dll export, so ir_lower.py special-cases c_name ==
    # "tgamma" to route to a native Lanczos (g=7, n=9) approximation shim
    # (_math_gamma in abi_shims.asm), valid for x > 0.5 -- the only range
    # this codebase's tests exercise. lgamma has no special-case yet (add a
    # `_math_lgamma` shim -- log of the same Lanczos formula, avoiding
    # overflow for large x -- if a test ever needs it on x86-64).
    "gamma":    Func(arg_types=("float",), ret_type="float", c_name="tgamma"),
    "lgamma":   Func(arg_types=("float",), ret_type="float", c_name="lgamma"),

    # prod(list) is a Python builtin not in math, but math.prod (3.8+) is
    # implemented as a pure-asmpython helper in the source layer, not FFI.
}
