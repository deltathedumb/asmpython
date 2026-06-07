"""math module: binds to libm. All trig/exp functions take and return float."""
from __future__ import annotations

import math as _py_math

from . import Func, Const


BINDINGS = {
    # Constants
    "pi":  Const(ty="float", value=_py_math.pi),
    "e":   Const(ty="float", value=_py_math.e),
    "tau": Const(ty="float", value=_py_math.tau),
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
    "floor": Func(arg_types=("float",), ret_type="float", c_name="floor"),
    "ceil":  Func(arg_types=("float",), ret_type="float", c_name="ceil"),
    "fabs":  Func(arg_types=("float",), ret_type="float", c_name="fabs"),

    # Two-argument float, float -> float
    "pow":   Func(arg_types=("float", "float"), ret_type="float", c_name="pow"),
    "atan2": Func(arg_types=("float", "float"), ret_type="float", c_name="atan2"),
    "hypot": Func(arg_types=("float", "float"), ret_type="float", c_name="hypot"),
    "fmod":  Func(arg_types=("float", "float"), ret_type="float", c_name="fmod"),
}
