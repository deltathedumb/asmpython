"""TCP, over the host-service `net` group.

THE GROUP WAS DECLARED AND IMPLEMENTED NOWHERE. `objects/hostsvc.py` has
named `host_net_connect`/`listen`/`accept`/`read`/`write`/`close` since it
was written, with a comment saying streams only and blocking, and no
backend answered any of them -- so `docs/STDLIB.md` listed `socket` under
"NEEDS THE FLOOR TO GROW" and the floor already had the shape of the
answer. It is now implemented twice: `objects/hostsvc_host.py` calls CPython's
`socket`, and `objects/hostsvc.py`'s `C_SOURCE["net"]` calls BSD sockets
(and Winsock). One operation was added to the group to make it usable at
all -- `host_net_port`, so a program that asks for an ephemeral port with
`bind(("", 0))` can find out which one it got.

COVERAGE: `socket(AF_INET, SOCK_STREAM)` -- `connect`, `bind`, `listen`,
`accept`, `send`, `sendall`, `recv`, `recv_into`, `close`, `fileno`,
`getsockname` (the port half), `detach`, and the context-manager
protocol; `create_connection`, `create_server`; `AF_INET`,
`SOCK_STREAM`, `SOL_SOCKET`, `SO_REUSEADDR`, `SHUT_RD`/`SHUT_WR`/
`SHUT_RDWR`; `error` (an alias of `OSError`, as CPython's is), `timeout`,
`gaierror`, `herror`; `has_ipv6`. A closed socket refuses every operation
with `OSError`, as CPython's does.

STREAMS ONLY AND BLOCKING, which is the group's contract rather than a
choice made here. Each of the following is refused BY NAME rather than
approximated, and each is a bigger contract than the whole group:

  * `SOCK_DGRAM` and `sendto`/`recvfrom` -- datagrams are a different
    protocol with a different addressing model, not a flag on this one.
  * `settimeout`/`setblocking`/`gettimeout` for anything but the blocking
    default, and `select`/`poll` -- readiness needs an operation the
    group does not have, and a socket that claims to be non-blocking and
    blocks is worse than one that says it cannot be.
  * `getaddrinfo`/`gethostbyname` -- resolution needs a resolver, a
    timeout and an error vocabulary of its own. AN ADDRESS IS NUMERIC
    HERE: `connect(("127.0.0.1", 8080))` works and
    `connect(("example.com", 80))` raises `gaierror` saying so, which is
    the same exception CPython raises when a name does not resolve.
  * `AF_INET6`, `AF_UNIX`, `ssl`, `setsockopt` for anything but
    `SO_REUSEADDR` (which `create_server` sets for you), `dup`, and
    socket objects as files (`makefile`).

BOTH ENDS IN ONE PROGRAM WORK, which is worth stating because this
runtime has one thread and `accept()` blocks. Connecting to a listening
socket completes as soon as the kernel queues the connection -- that is
what the backlog IS -- so `listen()`, then `connect()`, then `accept()`
runs to completion on one thread, and that is the shape
`tests/stdlib/socket.py` uses.
"""


#: `objects/hostsvc.py`'s error codes. Written out for the reason
#: `bundled/os.py` writes them out: `hostsvc` is a frontend/backend module
#: and a bundled module cannot import from the compiler that compiles it.
_HOST_ERR = -1
_HOST_ENOENT = -2
_HOST_EACCES = -3
_HOST_EEXIST = -4
_HOST_EAGAIN = -7
_HOST_EPIPE = -8
_HOST_EINVAL = -9

AF_INET = 2
AF_UNSPEC = 0
SOCK_STREAM = 1
SOL_SOCKET = 1
SO_REUSEADDR = 2
SHUT_RD = 0
SHUT_WR = 1
SHUT_RDWR = 2
#: There is no IPv6 in the `net` group, and saying so is more useful than
#: leaving the name undefined -- a program that branches on this gets the
#: right branch instead of a NameError.
has_ipv6 = False


#: CPython'S `socket.error` IS `OSError`, since 3.3. Reproduced as the same
#: aliasing rather than as a subclass, so `except OSError` catches what this
#: module raises exactly as it does there.
error = OSError


class timeout(OSError):
    pass


class gaierror(OSError):
    pass


class herror(OSError):
    pass


def _oops(code, what):
    """One host-service code as the exception CPython would raise."""
    if code == _HOST_EINVAL:
        raise OSError(what + ": invalid argument")
    if code == _HOST_EACCES:
        raise PermissionError(what + ": permission denied")
    if code == _HOST_EPIPE:
        raise BrokenPipeError(what + ": the peer has gone away")
    if code == _HOST_EAGAIN:
        raise BlockingIOError(what + ": would block")
    raise OSError(what + ": failed")


def _numeric(host):
    """A host that is already an address, or a `gaierror` saying why not.

    THE DOTTED-QUAD CHECK IS THE WHOLE RESOLVER, and the refusal is the
    honest half of that: a name this cannot resolve raises the exception
    CPython raises for a name IT cannot resolve, so a program's error
    path is the same one.
    """
    if host == "" or host == "0.0.0.0":
        # THE WILDCARD. `bind(("", port))` is how every server spells "all
        # interfaces"; the group binds loopback, which is what a program on
        # this runtime can reach anyway.
        return "127.0.0.1"
    if host == "localhost":
        return "127.0.0.1"
    parts = host.split(".")
    if len(parts) == 4:
        ok = True
        for part in parts:
            if not part.isdigit() or int(part) > 255 or part == "":
                ok = False
        if ok:
            return host
    raise gaierror("[Errno -2] Name or service not known: " + repr(host)
                   + " -- this build resolves no names; see the socket "
                     "module docstring")


def _address(pair, what):
    """`(host, port)`, checked. CPython's `TypeError` for anything else."""
    if not isinstance(pair, tuple) or len(pair) != 2:
        raise TypeError(what + "(): AF_INET address must be a pair "
                               "(host, port)")
    host, port = pair
    if not isinstance(port, int):
        raise TypeError("an integer is required (got type "
                        + type(port).__name__ + ")")
    if port < 0 or port > 65535:
        raise OverflowError("getsockaddrarg: port must be 0-65535.")
    return _numeric(host), port


class socket:
    """One TCP endpoint.

    THE DESCRIPTOR IS THE OBJECT'S WHOLE STATE, and `-1` means closed --
    the same convention CPython's `socket` uses and the same one
    `fileno()` reports, so a program that checks `s.fileno() == -1` gets
    the right answer.
    """

    def __init__(self, family=AF_INET, type=SOCK_STREAM, proto=0,
                 fileno=None):
        if family != AF_INET:
            raise OSError("only AF_INET is available: this build's network "
                          "is IPv4 streams -- see the socket module "
                          "docstring")
        if type != SOCK_STREAM:
            raise OSError("only SOCK_STREAM is available: datagrams are a "
                          "different contract -- see the socket module "
                          "docstring")
        self.family = family
        self.type = type
        self.proto = proto
        # LAZY, and that is not an optimisation. The group has no "make a
        # socket" operation -- `connect` and `listen` each make their own,
        # because a socket that is neither is a thing the operating system
        # has and this contract does not. So the descriptor appears at the
        # first of those, and `fileno()` answers -1 until then exactly as it
        # does for a CPython socket that has been closed.
        self._fd = -1 if fileno is None else int(fileno)
        self._listening = False

    # ── the descriptor ──────────────────────────────────────────────────
    def fileno(self):
        return self._fd

    def _live(self, what):
        if self._fd < 0 and not self._pending(what):
            raise OSError("[Errno 9] Bad file descriptor")
        return self._fd

    def _pending(self, what):
        """True while a not-yet-connected socket is still allowed to be
        used -- only `connect` and `bind` may act on one."""
        return what == "connect" or what == "bind"

    def detach(self):
        fd = self._fd
        self._fd = -1
        return fd

    def close(self):
        if self._fd < 0:
            # CLOSING TWICE IS FINE, in CPython and here: `close()` on a
            # closed socket is a no-op rather than an error, which is what
            # makes `with` and `try/finally` safe to write.
            return
        fd = self._fd
        self._fd = -1
        host_net_close(fd)

    def shutdown(self, how):
        # THE GROUP HAS NO HALF-CLOSE, so this is a full close for
        # `SHUT_RDWR` and a no-op otherwise -- named rather than refused,
        # because a program that calls `shutdown(SHUT_WR)` before `close()`
        # is being polite about the peer's `recv` and is not depending on
        # anything this cannot do.
        if how == SHUT_RDWR:
            self.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __repr__(self):
        return "<socket.socket fd=%d, family=%d, type=%d, proto=%d>" % (
            self._fd, self.family, self.type, self.proto)

    # ── connecting ──────────────────────────────────────────────────────
    def connect(self, address):
        host, port = _address(address, "connect")
        if self._fd >= 0:
            raise OSError("[Errno 106] Transport endpoint is already "
                          "connected")
        got = host_net_connect(host, len(host), port)
        if got < 0:
            if got == _HOST_ERR:
                raise ConnectionRefusedError(
                    "[Errno 111] Connection refused")
            _oops(got, "connect")
        self._fd = got

    def connect_ex(self, address):
        """`connect`, answering an error number instead of raising -- and
        0 on success, exactly as CPython's does. The number is this
        layer's rather than `errno`'s, since that is the only one that
        means the same thing on every target."""
        try:
            self.connect(address)
        except OSError:
            return _HOST_ERR
        return 0

    # ── listening ───────────────────────────────────────────────────────
    def bind(self, address):
        """Remember where to listen. THE SOCKET IS MADE BY `listen()`,
        because the group's `host_net_listen` binds and listens in one
        operation -- so this records the address and `listen` uses it,
        which is invisible to a program that calls them in order."""
        host, port = _address(address, "bind")
        if self._fd >= 0:
            raise OSError("[Errno 22] Invalid argument")
        self._bind_port = port

    def listen(self, backlog=128):
        port = getattr(self, "_bind_port", None)
        if port is None:
            raise OSError("[Errno 22] Invalid argument: listen() before "
                          "bind()")
        if self._fd >= 0:
            raise OSError("[Errno 22] Invalid argument")
        got = host_net_listen(port, backlog)
        if got < 0:
            if got == _HOST_ERR:
                raise OSError("[Errno 98] Address already in use")
            _oops(got, "listen")
        self._fd = got
        self._listening = True

    def accept(self):
        """`(connection, address)`, as CPython answers it.

        THE ADDRESS IS THE PEER'S and the group does not report it, so it
        is `('127.0.0.1', 0)`: the host is right (this network is
        loopback) and the port is the one field that is not known. A
        program that logs it sees a zero rather than a wrong number.
        """
        fd = self._live("accept")
        if not self._listening:
            raise OSError("[Errno 22] Invalid argument: accept() on a "
                          "socket that is not listening")
        got = host_net_accept(fd)
        if got < 0:
            _oops(got, "accept")
        conn = socket(self.family, self.type, self.proto, fileno=got)
        return conn, ("127.0.0.1", 0)

    def getsockname(self):
        """`(host, port)` for this socket's own end.

        THE PORT IS THE POINT: `bind(("", 0))` asks for a free port and
        this is how a program learns which one it got, which is what
        every ephemeral server and every test needs.
        """
        fd = self._live("getsockname")
        got = host_net_port(fd)
        if got < 0:
            _oops(got, "getsockname")
        return ("127.0.0.1", got)

    def getpeername(self):
        fd = self._live("getpeername")
        return ("127.0.0.1", 0)

    # ── the stream ──────────────────────────────────────────────────────
    def send(self, data, flags=0):
        """As many bytes as the operating system took, which may be fewer
        than offered -- CPython's `send` says the same and `sendall` is
        the loop over it."""
        fd = self._live("send")
        raw = bytes(data)
        got = host_net_write(fd, raw, len(raw))
        if got < 0:
            _oops(got, "send")
        return got

    def sendall(self, data, flags=0):
        raw = bytes(data)
        sent = 0
        while sent < len(raw):
            got = self.send(raw[sent:])
            if got <= 0:
                raise BrokenPipeError("[Errno 32] Broken pipe")
            sent = sent + got
        return None

    def recv(self, bufsize, flags=0):
        """Up to `bufsize` bytes. `b""` MEANS THE PEER CLOSED -- the whole
        end-of-stream protocol, in CPython and here."""
        fd = self._live("recv")
        if bufsize < 0:
            raise ValueError("negative buffersize in recv")
        buf = bytearray(bufsize)
        got = host_net_read(fd, buf, bufsize)
        if got < 0:
            _oops(got, "recv")
        return bytes(buf[:got])

    def recv_into(self, buffer, nbytes=0, flags=0):
        fd = self._live("recv_into")
        want = nbytes if nbytes > 0 else len(buffer)
        got = host_net_read(fd, buffer, want)
        if got < 0:
            _oops(got, "recv_into")
        return got

    def setsockopt(self, level, option, value):
        """`SO_REUSEADDR` IS ALREADY SET on every listener this makes --
        see `host_net_listen` in both implementations -- so asking for it
        succeeds and asking for anything else says what it cannot do."""
        if level == SOL_SOCKET and option == SO_REUSEADDR:
            return None
        raise OSError("setsockopt: only SO_REUSEADDR is available, and it "
                      "is already set -- see the socket module docstring")

    def getsockopt(self, level, option, buflen=0):
        if level == SOL_SOCKET and option == SO_REUSEADDR:
            return 1
        raise OSError("getsockopt: only SO_REUSEADDR is available -- see "
                      "the socket module docstring")

    def setblocking(self, flag):
        if not flag:
            raise OSError("setblocking(False) is not available: this "
                          "build's sockets are blocking -- see the socket "
                          "module docstring")

    def gettimeout(self):
        """None, meaning blocking, which is what these sockets are."""
        return None

    def settimeout(self, value):
        if value is not None:
            raise OSError("settimeout is not available: this build's "
                          "sockets are blocking -- see the socket module "
                          "docstring")


def create_connection(address, timeout=None, source_address=None):
    """A connected socket, CPython's own convenience."""
    if source_address is not None:
        raise OSError("create_connection: source_address is not available "
                      "-- see the socket module docstring")
    sock = socket(AF_INET, SOCK_STREAM)
    sock.connect(address)
    return sock


def create_server(address, family=AF_INET, backlog=None,
                  reuse_port=False, dualstack_ipv6=False):
    """A bound, listening socket.

    `SO_REUSEADDR` is set, as CPython's `create_server` sets it -- see
    `host_net_listen`, which sets it in both implementations for the same
    reason: a listener that has just closed leaves the port in TIME_WAIT
    and the next run of the same program is refused.
    """
    if dualstack_ipv6:
        raise OSError("create_server: dualstack_ipv6 needs IPv6, which "
                      "this build's network does not have")
    if reuse_port:
        raise OSError("create_server: reuse_port is not available -- see "
                      "the socket module docstring")
    sock = socket(family, SOCK_STREAM)
    sock.bind(address)
    sock.listen(backlog if backlog is not None else 128)
    return sock


def inet_aton(address):
    """The four bytes of a dotted quad, network order."""
    parts = _numeric(address).split(".")
    return bytes([int(parts[0]), int(parts[1]), int(parts[2]),
                  int(parts[3])])


def inet_ntoa(packed):
    raw = bytes(packed)
    if len(raw) != 4:
        raise OSError("packed IP wrong length for inet_ntoa")
    return "%d.%d.%d.%d" % (raw[0], raw[1], raw[2], raw[3])


def htons(value):
    """Host to network, 16 bits. Written out rather than assumed: every
    platform this targets is little-endian, so this is always a swap --
    and being explicit is what makes it right on one that is not."""
    return ((value & 0xff) << 8) | ((value >> 8) & 0xff)


def ntohs(value):
    return htons(value)


def htonl(value):
    return (((value & 0xff) << 24) | ((value & 0xff00) << 8)
            | ((value >> 8) & 0xff00) | ((value >> 24) & 0xff))


def ntohl(value):
    return htonl(value)


def gethostname():
    """`'localhost'`. The group has no way to ask the machine its name,
    and the name of the only host this network can reach is that."""
    return "localhost"


def gethostbyname(name):
    return _numeric(name)


def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """One entry, for a numeric address. See the module docstring on why
    there is no resolver: a name raises `gaierror` from `_numeric`, which
    is the exception CPython raises for a name it cannot resolve."""
    address = _numeric(host if host is not None else "")
    return [(AF_INET, SOCK_STREAM, 6, "", (address, port if port else 0))]
