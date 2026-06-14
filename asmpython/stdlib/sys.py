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
    "version":      Const(ty="str", value="asmpython 1.0.2"),
    "maxsize":      Const(ty="int", value=9223372036854775807),
    "byteorder":    Const(ty="str", value="little"),
    "platform":     Const(ty="str", value="linux", value_windows="win32"),
    "exec_prefix":  Const(ty="str", value=""),
    "prefix":       Const(ty="str", value=""),
    # sys.argv -> list[str], built at program startup from argc/argv.
    "argv":         Const(ty="list", value="__sys_argv__", el_type="str"),
    # sys.stdin / stdout / stderr: FILE* handles (opaque int, not the int fd).
    # Use os.fgetc / os.fputs to perform I/O on them. The C symbol names
    # differ; on Windows UCRT streams live behind __acrt_iob_func(n).
    "_stdin":   Func(arg_types=(), ret_type="str", c_name="__sys_stdin__"),
    "_stdout":  Func(arg_types=(), ret_type="str", c_name="__sys_stdout__"),
    "_stderr":  Func(arg_types=(), ret_type="str", c_name="__sys_stderr__"),
}
