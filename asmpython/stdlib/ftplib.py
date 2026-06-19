"""ftplib: FTP protocol client.

Usage::

    import ftplib

    with ftplib.FTP("ftp.example.com") as ftp:
        ftp.login("user", "password")
        ftp.cwd("/pub")
        names = ftp.nlst()           # list filenames
        ftp.retrlines("LIST", print) # long listing
        ftp.retrbinary("RETR file.txt", open("file.txt","w").write)
"""
from __future__ import annotations

import socket as _socket

FTP_PORT: int = 21
MAXLINE: int = 8192
CRLF: str = "\r\n"
_GLOBAL_DEFAULT_TIMEOUT: float = -1.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class Error(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class error_reply(Error):
    pass


class error_temp(Error):
    pass


class error_perm(Error):
    pass


class error_proto(Error):
    pass


all_errors: list = [Error, error_reply, error_temp, error_perm, error_proto]


# ---------------------------------------------------------------------------
# FTP
# ---------------------------------------------------------------------------

class FTP:
    """Encapsulate FTP session."""

    debugging: int = 0
    host: str = ""
    port: int = FTP_PORT
    maxline: int = MAXLINE
    welcome: str = ""
    passiveserver: int = 1
    encoding: str = "utf-8"
    timeout: float = 30.0

    def __init__(self, host: str = "", user: str = "", passwd: str = "",
                 acct: str = "", timeout: float = 30.0,
                 source_address: list = []) -> None:
        self.timeout = timeout
        self._sock: _socket.socket = None
        self._recvbuf: str = ""
        if host:
            self.connect(host)
            if user:
                self.login(user, passwd, acct)

    # ------------------------------------------------------------------
    # Low-level
    # ------------------------------------------------------------------

    def _send(self, line: str) -> None:
        if self._sock is None:
            raise Error("not connected")
        self._sock.sendall(line + CRLF)

    def _recv_line(self) -> str:
        while "\n" not in self._recvbuf:
            chunk: str = self._sock.recv(4096)
            if not chunk:
                raise Error("connection closed")
            self._recvbuf = self._recvbuf + chunk
        nl: int = self._recvbuf.find("\n")
        line: str = self._recvbuf[:nl + 1]
        self._recvbuf = self._recvbuf[nl + 1:]
        return line.rstrip("\r\n")

    def _get_response(self) -> str:
        """Read a complete FTP response (may span multiple lines)."""
        line: str = self._recv_line()
        if len(line) >= 4 and line[3] == "-":
            code: str = line[:3]
            while True:
                next_line: str = self._recv_line()
                line = line + "\n" + next_line
                if next_line.startswith(code) and (len(next_line) < 4 or next_line[3] != "-"):
                    break
        return line

    def _get_reply(self) -> str:
        resp: str = self._get_response()
        code: str = resp[:1]
        if code == "4":
            raise error_temp(resp)
        if code == "5":
            raise error_perm(resp)
        if code not in ("1", "2", "3"):
            raise error_proto(resp)
        return resp

    def _cmd(self, cmd: str) -> str:
        self._send(cmd)
        return self._get_reply()

    # ------------------------------------------------------------------
    # Connection control
    # ------------------------------------------------------------------

    def connect(self, host: str = "", port: int = 0,
                timeout: float = -1.0, source_address: list = []) -> str:
        if not host:
            host = self.host
        if port == 0:
            port = self.port
        if timeout != -1.0:
            self.timeout = timeout
        self.host = host
        self.port = port
        self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect(host, port)
        self.welcome = self._get_reply()
        return self.welcome

    def getwelcome(self) -> str:
        return self.welcome

    def set_debuglevel(self, level: int) -> None:
        self.debugging = level

    def set_pasv(self, val: int) -> None:
        self.passiveserver = val

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def __enter__(self) -> FTP:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.close()
        return 0

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def login(self, user: str = "", passwd: str = "", acct: str = "") -> str:
        if not user:
            user = "anonymous"
        if not passwd:
            passwd = "anonymous@"
        resp: str = self._cmd("USER " + user)
        if resp[0] == "3":
            resp = self._cmd("PASS " + passwd)
        if resp[0] == "3":
            resp = self._cmd("ACCT " + acct)
        if resp[0] != "2":
            raise error_perm(resp)
        return resp

    # ------------------------------------------------------------------
    # Directory commands
    # ------------------------------------------------------------------

    def pwd(self) -> str:
        resp: str = self._cmd("PWD")
        start: int = resp.find('"')
        end: int = resp.rfind('"')
        if start >= 0 and end > start:
            return resp[start + 1:end]
        return ""

    def cwd(self, dirname: str) -> str:
        return self._cmd("CWD " + dirname)

    def mkd(self, dirname: str) -> str:
        resp: str = self._cmd("MKD " + dirname)
        start: int = resp.find('"')
        end: int = resp.rfind('"')
        if start >= 0 and end > start:
            return resp[start + 1:end]
        return dirname

    def rmd(self, dirname: str) -> str:
        return self._cmd("RMD " + dirname)

    def delete(self, filename: str) -> str:
        return self._cmd("DELE " + filename)

    def rename(self, fromname: str, toname: str) -> str:
        self._cmd("RNFR " + fromname)
        return self._cmd("RNTO " + toname)

    def size(self, filename: str) -> int:
        resp: str = self._cmd("SIZE " + filename)
        parts: list = resp.split()
        if len(parts) >= 2:
            return int(parts[1])
        return -1

    def nlst(self, arg: str = "") -> list:
        """Return a list of filenames from the current directory."""
        cmd: str = "NLST"
        if arg:
            cmd = cmd + " " + arg
        lines: list = []
        data_sock: _socket.socket = self._open_data_connection()
        self._cmd(cmd)
        data: str = ""
        while True:
            chunk: str = data_sock.recv(4096)
            if not chunk:
                break
            data = data + chunk
        data_sock.close()
        self._get_reply()
        for line in data.split("\n"):
            line = line.strip()
            if line:
                lines.append(line)
        return lines

    def retrlines(self, cmd: str, callback: int) -> str:
        """Retrieve lines from server, calling callback for each line."""
        data_sock: _socket.socket = self._open_data_connection()
        self._cmd(cmd)
        buf: str = ""
        while True:
            chunk: str = data_sock.recv(4096)
            if not chunk:
                break
            buf = buf + chunk
        data_sock.close()
        resp: str = self._get_reply()
        fn: int = callback
        for line in buf.split("\n"):
            fn(line.rstrip("\r"))
        return resp

    def retrbinary(self, cmd: str, callback: int, blocksize: int = 8192,
                   rest: int = 0) -> str:
        """Retrieve file in binary mode, calling callback with each block."""
        data_sock: _socket.socket = self._open_data_connection()
        self._cmd(cmd)
        fn: int = callback
        while True:
            chunk: str = data_sock.recv(blocksize)
            if not chunk:
                break
            fn(chunk)
        data_sock.close()
        return self._get_reply()

    def storlines(self, cmd: str, fp: object) -> str:
        """Store lines from fp to server."""
        data_sock: _socket.socket = self._open_data_connection()
        self._cmd(cmd)
        data_sock.close()
        return self._get_reply()

    def storbinary(self, cmd: str, fp: object, blocksize: int = 8192,
                   callback: int = 0, rest: int = 0) -> str:
        """Store binary data from fp to server."""
        data_sock: _socket.socket = self._open_data_connection()
        self._cmd(cmd)
        data_sock.close()
        return self._get_reply()

    def abort(self) -> str:
        return self._cmd("ABOR")

    def quit(self) -> str:
        resp: str = self._cmd("QUIT")
        self.close()
        return resp

    def sendcmd(self, cmd: str) -> str:
        return self._cmd(cmd)

    def voidcmd(self, cmd: str) -> str:
        resp: str = self._cmd(cmd)
        if resp[0] != "2":
            raise error_reply(resp)
        return resp

    def getmultiline(self) -> str:
        return self._get_response()

    def getresp(self) -> str:
        return self._get_reply()

    # ------------------------------------------------------------------
    # Data connection helpers
    # ------------------------------------------------------------------

    def _open_data_connection(self) -> _socket.socket:
        """Open a data connection (PASV or PORT mode)."""
        if self.passiveserver:
            return self._open_pasv()
        return self._open_port()

    def _open_pasv(self) -> _socket.socket:
        """Open a passive data connection."""
        resp: str = self._cmd("PASV")
        # Parse PASV response: 227 Entering Passive Mode (h1,h2,h3,h4,p1,p2)
        start: int = resp.find("(")
        end: int = resp.find(")")
        if start < 0 or end < 0:
            raise error_reply("invalid PASV response: " + resp)
        nums_str: str = resp[start + 1:end]
        nums: list = nums_str.split(",")
        if len(nums) != 6:
            raise error_reply("invalid PASV response: " + resp)
        host: str = nums[0] + "." + nums[1] + "." + nums[2] + "." + nums[3]
        port: int = int(nums[4]) * 256 + int(nums[5])
        data_sock: _socket.socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        data_sock.settimeout(self.timeout)
        data_sock.connect(host, port)
        return data_sock

    def _open_port(self) -> _socket.socket:
        """Open an active data connection (PORT mode — simplified)."""
        data_sock: _socket.socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        data_sock.bind("0.0.0.0", 0)
        data_sock.listen(1)
        addr: list = data_sock.getsockname()
        port: int = addr[1]
        host: str = self._sock.getsockname()[0]
        parts: list = host.split(".")
        p1: int = port >> 8
        p2: int = port & 0xFF
        port_cmd: str = "PORT " + parts[0] + "," + parts[1] + "," + parts[2] + "," + parts[3] + "," + str(p1) + "," + str(p2)
        self._cmd(port_cmd)
        conn_sock: _socket.socket = data_sock.accept()[0]
        data_sock.close()
        return conn_sock

    def makepasv(self) -> list:
        resp: str = self._cmd("PASV")
        start: int = resp.find("(")
        end: int = resp.find(")")
        if start < 0 or end < 0:
            return ["", 0]
        nums_str: str = resp[start + 1:end]
        nums: list = nums_str.split(",")
        if len(nums) != 6:
            return ["", 0]
        host: str = nums[0] + "." + nums[1] + "." + nums[2] + "." + nums[3]
        port: int = int(nums[4]) * 256 + int(nums[5])
        return [host, port]


class FTP_TLS(FTP):
    """FTP subclass with TLS support (raises NotImplementedError)."""

    def __init__(self, host: str = "", user: str = "", passwd: str = "",
                 acct: str = "", keyfile: str = "", certfile: str = "",
                 context: object = None, timeout: float = 30.0,
                 source_address: list = []) -> None:
        raise NotImplementedError(
            "ftplib.FTP_TLS requires ssl binding; not available in asmpython"
        )
