"""sys module: process control and environment."""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # sys.exit(code)
    "exit":     Func(arg_types=("int",),  ret_type="int", c_name="exit"),
    # sys.getpid() -> int
    "getpid":   Func(arg_types=(),        ret_type="int", c_name="getpid", c_name_windows="_getpid"),
    # sys.getenv(name) -> str  (returns empty string for missing vars currently)
    "getenv":   Func(arg_types=("str",),  ret_type="str", c_name="getenv"),
    # sys.abort() — unrecoverable crash
    "abort":    Func(arg_types=(),        ret_type="int", c_name="abort"),
    # Constants
    "version":  Const(ty="str",  value="asmpython 0.1"),
    "maxsize":  Const(ty="int",  value=9223372036854775807),
}
