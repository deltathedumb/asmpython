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
}
