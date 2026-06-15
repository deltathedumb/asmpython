"""socket module: BSD socket networking.

Maps Python's socket API to the _net_* NASM helpers (same backend as
asmlib.network). Socket handles are ints (fd on Linux, SOCKET on Windows).

On Windows, the linker automatically adds -lws2_32 when any _net_* symbol
is referenced.
"""
from __future__ import annotations

from . import Func, Const

BINDINGS: dict = {
    # ---- Socket lifecycle ----------------------------------------------------
    # socket.socket(family, type, proto) -> fd
    "socket":           Func(arg_types=("int", "int", "int"), ret_type="int",
                             c_name="socket"),
    # socket.bind(fd, addr_str, port) -> 0 on success
    "bind":             Func(arg_types=("int", "str", "int"), ret_type="int",
                             c_name="_net_bind"),
    # socket.connect(fd, addr_str, port) -> 0 on success
    "connect":          Func(arg_types=("int", "str", "int"), ret_type="int",
                             c_name="_net_connect"),
    # socket.listen(fd, backlog) -> 0 on success
    "listen":           Func(arg_types=("int", "int"),        ret_type="int",
                             c_name="listen"),
    # socket.accept(fd) -> new_fd
    "accept":           Func(arg_types=("int",),              ret_type="int",
                             c_name="_net_accept"),
    # socket.close(fd) -> 0
    "close":            Func(arg_types=("int",),              ret_type="int",
                             c_name="_net_close"),

    # ---- Data transfer -------------------------------------------------------
    # socket.send(fd, data_str, flags) -> bytes_sent
    "send":             Func(arg_types=("int", "str", "int"), ret_type="int",
                             c_name="_net_send"),
    # socket.recv(fd, bufsize) -> received_str
    "recv":             Func(arg_types=("int", "int"),        ret_type="str",
                             c_name="_net_recv"),
    # socket.send_all(fd, data_str) -> bytes_sent  (loops until done)
    "send_all":         Func(arg_types=("int", "str"),        ret_type="int",
                             c_name="_net_send_all"),

    # ---- Address / byte-order helpers ----------------------------------------
    "htons":            Func(arg_types=("int",), ret_type="int", c_name="htons"),
    "htonl":            Func(arg_types=("int",), ret_type="int", c_name="htonl"),
    "ntohs":            Func(arg_types=("int",), ret_type="int", c_name="ntohs"),
    "ntohl":            Func(arg_types=("int",), ret_type="int", c_name="ntohl"),
    # inet_addr("1.2.3.4") -> 32-bit int (network byte order)
    "inet_addr":        Func(arg_types=("str",), ret_type="int",
                             c_name="inet_addr"),
    # gethostbyname(host) -> dotted-decimal string  (via _net_gethostname stub)
    "gethostname":      Func(arg_types=(),       ret_type="str",
                             c_name="_net_gethostname"),
    # getlasterror() -> WSAGetLastError on Windows, errno on Linux
    "getlasterror":     Func(arg_types=(),       ret_type="int",
                             c_name="_net_errno"),

    # ---- setsockopt / getsockopt (raw int option) ----------------------------
    # setsockopt(fd, level, optname, value) -> 0 on success
    "setsockopt":       Func(arg_types=("int", "int", "int", "int"),
                             ret_type="int", c_name="_net_setsockopt"),
    # getsockopt_int(fd, level, optname) -> int value
    "getsockopt_int":   Func(arg_types=("int", "int", "int"), ret_type="int",
                             c_name="_net_getsockopt_int"),

    # ---- Address family constants --------------------------------------------
    "AF_INET":          Const(ty="int", value=2),
    "AF_INET6":         Const(ty="int", value=10),
    "AF_UNIX":          Const(ty="int", value=1),

    # ---- Socket type constants -----------------------------------------------
    "SOCK_STREAM":      Const(ty="int", value=1),
    "SOCK_DGRAM":       Const(ty="int", value=2),
    "SOCK_RAW":         Const(ty="int", value=3),

    # ---- Protocol constants --------------------------------------------------
    "IPPROTO_TCP":      Const(ty="int", value=6),
    "IPPROTO_UDP":      Const(ty="int", value=17),
    "IPPROTO_IP":       Const(ty="int", value=0),

    # ---- Socket option level/name constants ----------------------------------
    "SOL_SOCKET":       Const(ty="int", value=1),
    "SO_REUSEADDR":     Const(ty="int", value=2),
    "SO_KEEPALIVE":     Const(ty="int", value=9),
    "SO_RCVBUF":        Const(ty="int", value=8),
    "SO_SNDBUF":        Const(ty="int", value=7),
    "TCP_NODELAY":      Const(ty="int", value=1),

    # ---- Shutdown flags -----------------------------------------------------
    "SHUT_RD":          Const(ty="int", value=0),
    "SHUT_WR":          Const(ty="int", value=1),
    "SHUT_RDWR":        Const(ty="int", value=2),

    # ---- Send/recv flags ----------------------------------------------------
    "MSG_PEEK":         Const(ty="int", value=2),
    "MSG_WAITALL":      Const(ty="int", value=256),

    # ---- Special addresses --------------------------------------------------
    "INADDR_ANY":       Const(ty="int", value=0),
    "INADDR_LOOPBACK":  Const(ty="int", value=2130706433),  # 127.0.0.1
    "INADDR_BROADCAST": Const(ty="int", value=-1),

    # ---- Common ports (convenience) -----------------------------------------
    "PORT_HTTP":        Const(ty="int", value=80),
    "PORT_HTTPS":       Const(ty="int", value=443),
    "PORT_FTP":         Const(ty="int", value=21),
    "PORT_SSH":         Const(ty="int", value=22),
    "PORT_SMTP":        Const(ty="int", value=25),
    "PORT_DNS":         Const(ty="int", value=53),
}
