"""poplib: POP3 protocol client.

Connects to a POP3 server to retrieve email messages.

Usage::

    import poplib

    pop = poplib.POP3("mail.example.com")
    pop.user("username")
    pop.pass_("password")
    num, size = pop.stat()
    print(f"{num} messages, {size} bytes total")
    for i in range(1, num + 1):
        resp, lines, octets = pop.retr(i)
        print("\\n".join(lines))
    pop.quit()
"""
from __future__ import annotations

import socket as _socket

POP3_PORT: int = 110
POP3_SSL_PORT: int = 995
CR: str = "\r"
LF: str = "\n"
CRLF: str = "\r\n"
_MAXLINE: int = 2048


class error_proto(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class POP3:
    """POP3 client class."""

    encoding: str = "utf-8"

    def __init__(self, host: str, port: int = POP3_PORT,
                 timeout: float = 30.0) -> None:
        self.host: str = host
        self.port: int = port
        self.timeout: float = timeout
        self._sock: _socket.socket = None
        self._recvbuf: str = ""
        self.welcome: str = ""
        self._connect()

    def _connect(self) -> None:
        self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect(self.host, self.port)
        self.welcome = self._get_line()
        if not self.welcome.startswith("+OK"):
            raise error_proto("server rejected connection: " + self.welcome)

    def _send(self, line: str) -> None:
        self._sock.sendall(line + CRLF)

    def _get_line(self) -> str:
        while "\n" not in self._recvbuf:
            chunk: str = self._sock.recv(4096)
            if not chunk:
                raise error_proto("connection closed")
            self._recvbuf = self._recvbuf + chunk
        nl: int = self._recvbuf.find("\n")
        line: str = self._recvbuf[:nl + 1]
        self._recvbuf = self._recvbuf[nl + 1:]
        return line.rstrip("\r\n")

    def _command(self, cmd: str) -> str:
        self._send(cmd)
        resp: str = self._get_line()
        if resp.startswith("-ERR"):
            raise error_proto(resp)
        return resp

    def _command_long(self, cmd: str) -> list:
        """Send a command and read a multi-line response."""
        resp: str = self._command(cmd)
        lines: list = []
        while True:
            line: str = self._get_line()
            if line == ".":
                break
            if line.startswith(".."):
                line = line[1:]
            lines.append(line)
        return [resp, lines, 0]  # 0 = octets (not counted)

    # ------------------------------------------------------------------
    # POP3 commands
    # ------------------------------------------------------------------

    def getwelcome(self) -> str:
        return self.welcome

    def set_debuglevel(self, level: int) -> None:
        pass

    def user(self, user: str) -> str:
        return self._command("USER " + user)

    def pass_(self, pswd: str) -> str:
        return self._command("PASS " + pswd)

    def stat(self) -> list:
        """Return [num_messages, total_size]."""
        resp: str = self._command("STAT")
        parts: list = resp.split()
        if len(parts) >= 3:
            return [int(parts[1]), int(parts[2])]
        return [0, 0]

    def list(self, which: int = 0) -> list:
        """Return [resp, lines, octets] for message list."""
        if which:
            resp: str = self._command("LIST " + str(which))
            return [resp, [resp], len(resp)]
        return self._command_long("LIST")

    def retr(self, which: int) -> list:
        """Return [resp, lines, octets] for message *which*."""
        return self._command_long("RETR " + str(which))

    def dele(self, which: int) -> str:
        """Mark message *which* for deletion."""
        return self._command("DELE " + str(which))

    def noop(self) -> str:
        """Keep the connection alive."""
        return self._command("NOOP")

    def rset(self) -> str:
        """Unmark messages for deletion."""
        return self._command("RSET")

    def quit(self) -> str:
        """Quit and close the connection."""
        resp: str = self._command("QUIT")
        self.close()
        return resp

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def top(self, which: int, howmuch: int) -> list:
        """Return [resp, lines, octets] for first *howmuch* lines of message."""
        return self._command_long("TOP " + str(which) + " " + str(howmuch))

    def uidl(self, which: int = 0) -> list:
        """Return unique-id listing."""
        if which:
            resp: str = self._command("UIDL " + str(which))
            return [resp, [resp], len(resp)]
        return self._command_long("UIDL")

    def apop(self, user: str, secret: str) -> str:
        """APOP authentication (stub — requires MD5)."""
        raise NotImplementedError("APOP authentication requires hashlib support")

    def rpop(self, user: str) -> str:
        return self._command("RPOP " + user)

    def utf8(self) -> str:
        return self._command("UTF8")

    def capa(self) -> dict:
        """Return server capabilities."""
        resp_data: list = self._command_long("CAPA")
        caps: dict = {}
        for line in resp_data[1]:
            parts: list = line.split()
            if parts:
                caps[parts[0]] = parts[1:]
        return caps

    def stls(self, context: object = None) -> str:
        raise NotImplementedError("POP3.stls requires ssl binding")

    def __enter__(self) -> POP3:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        try:
            self.quit()
        except Exception:
            self.close()
        return 0

    def __repr__(self) -> str:
        return "POP3(" + self.host + ":" + str(self.port) + ")"


class POP3_SSL(POP3):
    """POP3 over SSL (raises NotImplementedError — no TLS in asmpython)."""

    def __init__(self, host: str, port: int = POP3_SSL_PORT,
                 keyfile: str = "", certfile: str = "",
                 timeout: float = 30.0, context: object = None) -> None:
        raise NotImplementedError(
            "poplib.POP3_SSL requires ssl binding; not available in asmpython"
        )
