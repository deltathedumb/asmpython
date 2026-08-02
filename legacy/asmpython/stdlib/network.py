"""network — BSD socket API (hosted targets: Linux/Windows).

All socket handles are passed as int (file descriptor on Linux,
SOCKET = uintptr_t on Windows — both fit in a 64-bit int slot).
Address families, socket types, and protocol numbers are exposed as
integer constants so user code doesn't need to hardcode magic numbers.

On Windows the linker must add -lws2_32 (Winsock2).  The driver adds
it automatically when this module is detected in the imported set.
On Linux the symbols live in libc; no extra library is needed.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # ---- Socket lifecycle ---------------------------------------------------
    "socket":       Func(arg_types=("int", "int", "int"), ret_type="int", c_name="socket"),
    "bind":         Func(arg_types=("int", "str", "int"), ret_type="int", c_name="_net_bind"),
    "connect":      Func(arg_types=("int", "str", "int"), ret_type="int", c_name="_net_connect"),
    "listen":       Func(arg_types=("int", "int"),        ret_type="int", c_name="listen"),
    "accept":       Func(arg_types=("int",),              ret_type="int", c_name="_net_accept"),
    "close":        Func(arg_types=("int",),              ret_type="int", c_name="_net_close"),

    # ---- Data transfer ------------------------------------------------------
    "send":         Func(arg_types=("int", "str", "int"), ret_type="int", c_name="_net_send"),
    "recv":         Func(arg_types=("int", "int"),        ret_type="str", c_name="_net_recv"),
    "send_all":     Func(arg_types=("int", "str"),        ret_type="int", c_name="_net_send_all"),

    # ---- Byte-order ---------------------------------------------------------
    "htons":        Func(arg_types=("int",),              ret_type="int", c_name="htons"),
    "htonl":        Func(arg_types=("int",),              ret_type="int", c_name="htonl"),
    "ntohs":        Func(arg_types=("int",),              ret_type="int", c_name="ntohs"),
    "ntohl":        Func(arg_types=("int",),              ret_type="int", c_name="ntohl"),

    # ---- Address helpers ----------------------------------------------------
    "inet_addr":    Func(arg_types=("str",),              ret_type="int", c_name="inet_addr"),
    "gethostname":  Func(arg_types=(),                    ret_type="str", c_name="_net_gethostname"),

    # ---- Error code ---------------------------------------------------------
    "errno":        Func(arg_types=(),                    ret_type="int", c_name="_net_errno"),

    # ---- Constants ----------------------------------------------------------
    "AF_INET":      Const(ty="int", value=2),
    "AF_INET6":     Const(ty="int", value=10),

    "SOCK_STREAM":  Const(ty="int", value=1),
    "SOCK_DGRAM":   Const(ty="int", value=2),
    "SOCK_RAW":     Const(ty="int", value=3),

    "MSG_PEEK":     Const(ty="int", value=2),
    "MSG_WAITALL":  Const(ty="int", value=256),

    "PORT_HTTP":    Const(ty="int", value=80),
    "PORT_HTTPS":   Const(ty="int", value=443),
    "PORT_FTP":     Const(ty="int", value=21),
    "PORT_SSH":     Const(ty="int", value=22),
    "PORT_SMTP":    Const(ty="int", value=25),
    "PORT_DNS":     Const(ty="int", value=53),
    "INADDR_ANY":   Const(ty="int", value=0),
}
