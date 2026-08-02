"""smtplib: SMTP protocol client.

Connects to an SMTP server via socket and provides methods for sending
email messages. Supports SMTP and ESMTP; no TLS/SSL (requires ssl module).

Usage::

    import smtplib

    with smtplib.SMTP("smtp.example.com", 25) as smtp:
        smtp.login("user@example.com", "password")
        smtp.sendmail(
            "from@example.com",
            ["to@example.com"],
            "Subject: Hello\\r\\n\\r\\nBody text"
        )
"""
from __future__ import annotations

import socket as _socket

# SMTP status codes
SMTP_PORT: int = 25
SMTP_SSL_PORT: int = 465
SMTP_SUBMISSION_PORT: int = 587

# SMTP reply codes
SMTPConnectError: int = 421
SMTPServerDisconnected: int = 0

OLDSTYLE_AUTH: str = "AUTH"
_MAXLINE: int = 8192
_MAXCHALLENGE: int = 5
CRLF: str = "\r\n"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SMTPException(Exception):
    """Base class for SMTP exceptions."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class SMTPServerDisconnectedException(SMTPException):
    pass


class SMTPResponseException(SMTPException):
    def __init__(self, code: int, msg: str) -> None:
        self.smtp_code: int = code
        self.smtp_error: str = msg
        self.msg: str = str(code) + " " + msg

    def __str__(self) -> str:
        return str(self.smtp_code) + ": " + self.smtp_error


class SMTPSenderRefused(SMTPResponseException):
    def __init__(self, code: int, msg: str, sender: str) -> None:
        self.smtp_code = code
        self.smtp_error = msg
        self.sender: str = sender
        self.msg = str(code) + " " + msg + " (sender: " + sender + ")"


class SMTPRecipientsRefused(SMTPException):
    def __init__(self, recipients: dict) -> None:
        self.recipients: dict = recipients
        self.msg = "All recipients refused"


class SMTPDataError(SMTPResponseException):
    pass


class SMTPConnectError2(SMTPResponseException):
    pass


class SMTPHeloError(SMTPResponseException):
    pass


class SMTPAuthenticationError(SMTPResponseException):
    pass


class SMTPNotSupportedError(SMTPException):
    pass


# ---------------------------------------------------------------------------
# SMTP connection object
# ---------------------------------------------------------------------------

class SMTP:
    """SMTP/ESMTP client class.

    Connects to an SMTP server and provides sendmail/quit/login methods.
    """

    SMTP_PORT: int = SMTP_PORT
    SMTP_SSL_PORT: int = SMTP_SSL_PORT
    _host: str = ""
    debuglevel: int = 0
    file: object = None

    def __init__(self, host: str = "", port: int = 0,
                 local_hostname: str = "", timeout: float = 30.0,
                 source_address: list = []) -> None:
        self.timeout: float = timeout
        self.esmtp_features: dict = {}
        self._sock: _socket.socket = None
        self._recvbuf: str = ""
        self.helo_resp: str = ""
        self.ehlo_resp: str = ""
        self.ehlo_msg: str = "ehlo"
        self.does_esmtp: int = 0
        self._local_hostname: str = local_hostname if local_hostname else "localhost"
        if host:
            self.connect(host, port, source_address)

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    def _send(self, data: str) -> None:
        self._sock.sendall(data)

    def _recv_line(self) -> str:
        while "\n" not in self._recvbuf:
            chunk: str = self._sock.recv(4096)
            if not chunk:
                break
            self._recvbuf = self._recvbuf + chunk
        nl: int = self._recvbuf.find("\n")
        if nl >= 0:
            line: str = self._recvbuf[:nl + 1]
            self._recvbuf = self._recvbuf[nl + 1:]
            return line.rstrip("\r\n")
        line = self._recvbuf
        self._recvbuf = ""
        return line.rstrip("\r\n")

    def _get_reply(self) -> list:
        """Read one SMTP reply. Returns [code, msg]."""
        code: int = 0
        msg: str = ""
        while True:
            line: str = self._recv_line()
            if len(line) < 4:
                break
            code_str: str = line[:3]
            code = int(code_str)
            continuation: str = line[3:4]
            msg_part: str = line[4:]
            msg = msg + msg_part
            if continuation != "-":
                break
            msg = msg + "\n"
        return [code, msg]

    def _send_cmd(self, cmd: str) -> list:
        self._send(cmd + CRLF)
        return self._get_reply()

    # ------------------------------------------------------------------
    # SMTP commands
    # ------------------------------------------------------------------

    def connect(self, host: str = "localhost", port: int = 0,
                source_address: list = []) -> list:
        """Connect to an SMTP server."""
        if port == 0:
            port = self.SMTP_PORT
        self._host = host
        self._sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect(host, port)
        reply: list = self._get_reply()
        if reply[0] != 220:
            self._sock.close()
            raise SMTPConnectError2(reply[0], reply[1])
        return reply

    def helo(self, name: str = "") -> list:
        """Send HELO command."""
        if not name:
            name = self._local_hostname
        reply: list = self._send_cmd("HELO " + name)
        self.helo_resp = reply[1]
        return reply

    def ehlo(self, name: str = "") -> list:
        """Send EHLO command (ESMTP)."""
        if not name:
            name = self._local_hostname
        reply: list = self._send_cmd("EHLO " + name)
        self.ehlo_resp = reply[1]
        if reply[0] == 250:
            self.does_esmtp = 1
            for line in reply[1].split("\n"):
                line = line.strip()
                if line:
                    parts: list = line.split(" ")
                    feat: str = parts[0].upper()
                    if len(parts) > 1:
                        params: str = parts[1]
                        self.esmtp_features[feat] = params
                    else:
                        self.esmtp_features[feat] = "1"
        return reply

    def ehlo_or_helo_if_needed(self) -> None:
        if not self.helo_resp and not self.ehlo_resp:
            reply: list = self.ehlo()
            if reply[0] != 250:
                self.helo()

    def has_extn(self, opt: str) -> int:
        return opt.upper() in self.esmtp_features

    def help(self, args: str = "") -> str:
        reply: list = self._send_cmd("HELP " + args)
        return reply[1]

    def rset(self) -> list:
        return self._send_cmd("RSET")

    def noop(self) -> list:
        return self._send_cmd("NOOP")

    def vrfy(self, address: str) -> list:
        return self._send_cmd("VRFY " + address)

    def mail(self, sender: str, options: list = []) -> list:
        optstr: str = ""
        for opt in options:
            optstr = optstr + " " + opt
        return self._send_cmd("MAIL FROM:<" + sender + ">" + optstr)

    def rcpt(self, recip: str, options: list = []) -> list:
        optstr: str = ""
        for opt in options:
            optstr = optstr + " " + opt
        return self._send_cmd("RCPT TO:<" + recip + ">" + optstr)

    def data(self, msg: str) -> list:
        reply: list = self._send_cmd("DATA")
        if reply[0] != 354:
            raise SMTPDataError(reply[0], reply[1])
        # Dot-stuff: prefix each line starting with '.' with another '.'
        lines: list = msg.split("\n")
        body: str = ""
        for line in lines:
            if line.startswith("."):
                body = body + "." + line + "\n"
            else:
                body = body + line + "\n"
        self._send(body + ".\r\n")
        return self._get_reply()

    def verify(self, address: str) -> list:
        return self.vrfy(address)

    def login(self, user: str, password: str, initial_response_ok: int = 1) -> list:
        """Authenticate using AUTH LOGIN or AUTH PLAIN."""
        import base64 as _b64
        self.ehlo_or_helo_if_needed()
        if "AUTH" not in self.esmtp_features:
            raise SMTPNotSupportedError("SMTP AUTH extension not supported by server")
        # Try AUTH PLAIN first
        creds: str = "\x00" + user + "\x00" + password
        encoded: str = _b64.b64encode(creds)
        reply: list = self._send_cmd("AUTH PLAIN " + encoded)
        if reply[0] == 235:
            return reply
        # Try AUTH LOGIN
        reply = self._send_cmd("AUTH LOGIN")
        if reply[0] != 334:
            raise SMTPAuthenticationError(reply[0], reply[1])
        self._send(_b64.b64encode(user) + CRLF)
        reply = self._get_reply()
        if reply[0] != 334:
            raise SMTPAuthenticationError(reply[0], reply[1])
        self._send(_b64.b64encode(password) + CRLF)
        reply = self._get_reply()
        if reply[0] != 235:
            raise SMTPAuthenticationError(reply[0], reply[1])
        return reply

    def sendmail(self, from_addr: str, to_addrs: list, msg: str,
                 mail_options: list = [], rcpt_options: list = []) -> dict:
        """Send a message to one or more recipients.

        Returns a dict of refused recipients (empty dict on success).
        """
        self.ehlo_or_helo_if_needed()
        ecode: int = 0
        emsg: str = ""
        reply: list = self.mail(from_addr, mail_options)
        if reply[0] != 250:
            if reply[0] == 421:
                self.close()
            else:
                self.rset()
            raise SMTPSenderRefused(reply[0], reply[1], from_addr)
        refused: dict = {}
        for addr in to_addrs:
            r: list = self.rcpt(addr, rcpt_options)
            if r[0] not in (250, 251):
                refused[addr] = str(r[0]) + " " + r[1]
        if len(refused) == len(to_addrs):
            raise SMTPRecipientsRefused(refused)
        dr: list = self.data(msg)
        if dr[0] != 250:
            if dr[0] == 421:
                self.close()
            else:
                self.rset()
            raise SMTPDataError(dr[0], dr[1])
        return refused

    def send_message(self, msg: object, from_addr: str = "",
                     to_addrs: list = [], mail_options: list = [],
                     rcpt_options: list = []) -> dict:
        """Send an email.Message object (stub — pass the formatted string)."""
        return {}

    def quit(self) -> list:
        """Send QUIT and close the connection."""
        reply: list = self._send_cmd("QUIT")
        self.close()
        return reply

    def close(self) -> None:
        """Close the SMTP connection."""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def starttls(self, keyfile: str = "", certfile: str = "",
                 context: object = None) -> list:
        """Send STARTTLS (raises NotImplementedError — no TLS in asmpython)."""
        raise NotImplementedError("smtplib.SMTP.starttls requires ssl binding")

    def __enter__(self) -> SMTP:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        if self._sock is not None:
            try:
                self.quit()
            except Exception:
                self.close()
        return 0

    def __repr__(self) -> str:
        return "SMTP(" + self._host + ")"


class SMTP_SSL(SMTP):
    """SMTP over TLS (raises NotImplementedError — no TLS in asmpython)."""

    def __init__(self, host: str = "", port: int = 0,
                 local_hostname: str = "", keyfile: str = "",
                 certfile: str = "", timeout: float = 30.0,
                 source_address: list = [], context: object = None) -> None:
        raise NotImplementedError(
            "smtplib.SMTP_SSL requires ssl binding; not available in asmpython"
        )


class LMTP(SMTP):
    """LMTP protocol client (identical to SMTP with different greeting)."""

    LMTP_PORT: int = 2003

    def __init__(self, host: str = "", port: int = 0,
                 local_hostname: str = "", source_address: list = [],
                 timeout: float = 30.0) -> None:
        super().__init__(host, port if port else self.LMTP_PORT,
                         local_hostname, timeout, source_address)
