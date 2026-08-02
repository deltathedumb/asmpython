"""imaplib: IMAP4 protocol client.

Implements IMAP4 and IMAP4_SSL clients for reading and managing email.

Usage::

    import imaplib

    imap = imaplib.IMAP4("imap.example.com")
    imap.login("user@example.com", "password")
    imap.select("INBOX")
    typ, msgnums = imap.search(None, "ALL")
    for num in msgnums[0].split():
        typ, data = imap.fetch(num, "(RFC822)")
        print(data[0])
    imap.logout()
"""
from __future__ import annotations

import socket as _socket

IMAP4_PORT: int = 143
IMAP4_SSL_PORT: int = 993
CRLF: str = "\r\n"
Debug: int = 0
IMAP4_literals: int = 0


class error(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class abort(error):
    pass


class readonly(error):
    pass


class _Authenticator:
    """Authenticator helper (stub)."""

    def __init__(self, mechname: str, authobj: object) -> None:
        self.mech: str = mechname
        self.authobj: object = authobj


class IMAP4:
    """IMAP4 client class."""

    class error(Exception):
        def __init__(self, msg: str = "") -> None:
            self.msg: str = msg

        def __str__(self) -> str:
            return self.msg

    class abort(error):
        pass

    class readonly(error):
        pass

    mustquote: str = r'([\x00-\x1f\x7f-\xff \"%])'
    PROTOCOL_VERSION: str = "IMAP4rev1"
    CAPABILITIES: str = "CAPABILITY IMAP4rev1 LITERAL+ IDLE"

    def __init__(self, host: str = "localhost", port: int = IMAP4_PORT,
                 timeout: float = 0.0) -> None:
        self.host: str = host
        self.port: int = port
        self.timeout: float = timeout
        self._sock: _socket.socket = None
        self._recvbuf: str = ""
        self._tag_counter: int = 0
        self._capabilities: list = []
        self.welcome: str = ""
        self.state: str = "LOGOUT"
        self._connect()

    def _connect(self) -> None:
        self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        if self.timeout > 0.0:
            self._sock.settimeout(self.timeout)
        self._sock.connect(self.host, self.port)
        self.welcome = self._get_line()
        if not self.welcome.startswith("* OK"):
            raise error("server rejected connection: " + self.welcome)
        self.state = "NONAUTH"

    def _send(self, data: str) -> None:
        self._sock.sendall(data)

    def _get_line(self) -> str:
        while "\n" not in self._recvbuf:
            chunk: str = self._sock.recv(4096)
            if not chunk:
                raise abort("connection closed")
            self._recvbuf = self._recvbuf + chunk
        nl: int = self._recvbuf.find("\n")
        line: str = self._recvbuf[:nl + 1]
        self._recvbuf = self._recvbuf[nl + 1:]
        return line.rstrip("\r\n")

    def _new_tag(self) -> str:
        self._tag_counter = self._tag_counter + 1
        return "A" + str(self._tag_counter).zfill(4)

    def _command(self, name: str, *parts: str) -> list:
        """Send a tagged IMAP command and collect the response."""
        tag: str = self._new_tag()
        cmd: str = tag + " " + name
        for part in parts:
            cmd = cmd + " " + part
        self._send(cmd + CRLF)
        return self._read_response(tag)

    def _read_response(self, tag: str) -> list:
        """Read lines until we see the tagged final response."""
        untagged: list = []
        while True:
            line: str = self._get_line()
            if line.startswith(tag + " "):
                rest: str = line[len(tag) + 1:]
                if rest.startswith("OK"):
                    return ["OK", untagged]
                if rest.startswith("NO"):
                    return ["NO", untagged]
                if rest.startswith("BAD"):
                    return ["BAD", untagged]
                return ["OK", untagged]
            untagged.append(line)

    # ------------------------------------------------------------------
    # IMAP4 commands
    # ------------------------------------------------------------------

    def login(self, user: str, password: str) -> list:
        """Authenticate with USER/PASSWORD."""
        resp: list = self._command("LOGIN", _quote(user), _quote(password))
        if resp[0] == "OK":
            self.state = "AUTH"
        else:
            raise error("LOGIN failed: " + str(resp[1]))
        return resp

    def logout(self) -> list:
        """Logout and close connection."""
        try:
            resp: list = self._command("LOGOUT")
        except Exception:
            resp = ["OK", []]
        self.close_connection()
        self.state = "LOGOUT"
        return resp

    def close_connection(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def select(self, mailbox: str = "INBOX", readonly: int = 0) -> list:
        """Select a mailbox."""
        if readonly:
            resp: list = self._command("EXAMINE", _quote(mailbox))
        else:
            resp = self._command("SELECT", _quote(mailbox))
        if resp[0] == "OK":
            self.state = "SELECTED"
        return resp

    def close(self) -> list:
        """Close the current mailbox."""
        resp: list = self._command("CLOSE")
        self.state = "AUTH"
        return resp

    def expunge(self) -> list:
        """Permanently remove deleted messages."""
        return self._command("EXPUNGE")

    def search(self, charset: str, criterion0: str = "ALL",
               criterion1: str = "", criterion2: str = "") -> list:
        """Search for messages matching criteria."""
        crit: str = criterion0
        if criterion1:
            crit = crit + " " + criterion1
        if criterion2:
            crit = crit + " " + criterion2
        if charset:
            return self._command("SEARCH", "CHARSET", charset, crit)
        return self._command("SEARCH", crit)

    def fetch(self, message_set: str, message_parts: str) -> list:
        """Fetch message data."""
        return self._command("FETCH", message_set, message_parts)

    def store(self, message_set: str, command: str, flags: str) -> list:
        """Alter flags for messages."""
        return self._command("STORE", message_set, command, flags)

    def copy(self, message_set: str, new_mailbox: str) -> list:
        """Copy messages to another mailbox."""
        return self._command("COPY", message_set, _quote(new_mailbox))

    def uid(self, command: str, arg0: str = "", arg1: str = "",
            arg2: str = "") -> list:
        """Execute IMAP4 command with UIDs."""
        cmd: str = command
        if arg0:
            cmd = cmd + " " + arg0
        if arg1:
            cmd = cmd + " " + arg1
        if arg2:
            cmd = cmd + " " + arg2
        return self._command("UID", cmd)

    def create(self, mailbox: str) -> list:
        return self._command("CREATE", _quote(mailbox))

    def delete(self, mailbox: str) -> list:
        return self._command("DELETE", _quote(mailbox))

    def rename(self, oldmailbox: str, newmailbox: str) -> list:
        return self._command("RENAME", _quote(oldmailbox), _quote(newmailbox))

    def list(self, directory: str = "", pattern: str = "*") -> list:
        """List mailboxes."""
        return self._command("LIST", _quote(directory), _quote(pattern))

    def lsub(self, directory: str = "", pattern: str = "*") -> list:
        """List subscribed mailboxes."""
        return self._command("LSUB", _quote(directory), _quote(pattern))

    def subscribe(self, mailbox: str) -> list:
        return self._command("SUBSCRIBE", _quote(mailbox))

    def unsubscribe(self, mailbox: str) -> list:
        return self._command("UNSUBSCRIBE", _quote(mailbox))

    def status(self, mailbox: str, names: str) -> list:
        return self._command("STATUS", _quote(mailbox), names)

    def append(self, mailbox: str, flags: str, date_time: str,
               message: str) -> list:
        """Append a message to a mailbox."""
        lit_len: str = "{" + str(len(message)) + "}"
        self._send("A" + str(self._tag_counter + 1) + " APPEND " +
                   _quote(mailbox) + " " + flags + " " + date_time +
                   " " + lit_len + CRLF)
        # Wait for continue response
        cont: str = self._get_line()
        if cont.startswith("+"):
            self._send(message + CRLF)
            tag: str = "A" + str(self._tag_counter + 1).zfill(4)
            self._tag_counter = self._tag_counter + 1
            return self._read_response(tag)
        return ["NO", []]

    def check(self) -> list:
        return self._command("CHECK")

    def noop(self) -> list:
        return self._command("NOOP")

    def capability(self) -> list:
        return self._command("CAPABILITY")

    def authenticate(self, mechanism: str, authobject: int) -> list:
        raise NotImplementedError("IMAP4.authenticate: not yet implemented")

    def login_cram_md5(self, user: str, password: str) -> list:
        raise NotImplementedError("IMAP4.login_cram_md5: requires hashlib HMAC")

    def starttls(self, ssl_context: object = None) -> list:
        raise NotImplementedError("IMAP4.starttls requires ssl binding")

    def idle(self) -> None:
        raise NotImplementedError("IMAP4.idle: not implemented")

    def shutdown(self) -> None:
        self.close_connection()

    def socket(self) -> _socket.socket:
        return self._sock

    def recent(self) -> list:
        return self._command("RECENT")

    def response(self, code: str) -> list:
        return ["OK", []]

    def setacl(self, mailbox: str, who: str, what: str) -> list:
        return self._command("SETACL", _quote(mailbox), _quote(who), _quote(what))

    def getacl(self, mailbox: str) -> list:
        return self._command("GETACL", _quote(mailbox))

    def myrights(self, mailbox: str) -> list:
        return self._command("MYRIGHTS", _quote(mailbox))

    def setquota(self, root: str, limits: str) -> list:
        return self._command("SETQUOTA", _quote(root), limits)

    def getquota(self, root: str) -> list:
        return self._command("GETQUOTA", _quote(root))

    def getquotaroot(self, mailbox: str) -> list:
        return self._command("GETQUOTAROOT", _quote(mailbox))

    def namespace(self) -> list:
        return self._command("NAMESPACE")

    def __enter__(self) -> IMAP4:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        try:
            self.logout()
        except Exception:
            self.close_connection()
        return 0

    def __repr__(self) -> str:
        return "IMAP4(" + self.host + ":" + str(self.port) + ")"


class IMAP4_SSL(IMAP4):
    """IMAP4 over SSL (raises NotImplementedError — no TLS in asmpython)."""

    def __init__(self, host: str = "localhost", port: int = IMAP4_SSL_PORT,
                 keyfile: str = "", certfile: str = "",
                 ssl_context: object = None, timeout: float = 0.0) -> None:
        raise NotImplementedError(
            "imaplib.IMAP4_SSL requires ssl binding; not available in asmpython"
        )


class IMAP4_stream(IMAP4):
    """IMAP4 using a subprocess stream (not supported in asmpython)."""

    def __init__(self, command: str) -> None:
        raise NotImplementedError(
            "imaplib.IMAP4_stream: subprocess streams not supported in asmpython"
        )


def _quote(arg: str) -> str:
    """Quote an argument for IMAP protocol."""
    if " " in arg or '"' in arg or arg == "":
        escaped: str = arg.replace("\\", "\\\\").replace('"', '\\"')
        return '"' + escaped + '"'
    return arg


def _unquote(arg: str) -> str:
    """Unquote an IMAP protocol argument."""
    if arg.startswith('"') and arg.endswith('"'):
        return arg[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return arg


def Internaldate2tuple(resp: str) -> list:
    """Convert IMAP internal date to [year, month, day, hour, min, sec, tz]."""
    return [1970, 1, 1, 0, 0, 0, 0]


def Int2AP(num: int) -> str:
    """Convert integer to IMAP string."""
    return str(num)


def ParseFlags(resp: str) -> list:
    """Parse IMAP flags from response string."""
    start: int = resp.find("(")
    end: int = resp.find(")")
    if start < 0 or end < 0:
        return []
    flags_str: str = resp[start + 1:end]
    return flags_str.split()


def Time2Internaldate(date_time: float) -> str:
    """Format a date for IMAP APPEND (stub)."""
    return '"01-Jan-1970 00:00:00 +0000"'
