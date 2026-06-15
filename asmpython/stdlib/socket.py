"""socket module: network interface stubs.

Full socket networking requires OS-level non-blocking I/O and select()
which asmpython does not yet support. These stubs allow code that imports
socket to compile and allow querying of constants and hostname/address
functions through the C library.
"""
from __future__ import annotations


# Address families
AF_INET: int = 2
AF_INET6: int = 10
AF_UNIX: int = 1

# Socket types
SOCK_STREAM: int = 1
SOCK_DGRAM: int = 2
SOCK_RAW: int = 3

# Protocol families
IPPROTO_TCP: int = 6
IPPROTO_UDP: int = 17
IPPROTO_IP: int = 0

# Socket options
SOL_SOCKET: int = 1
SO_REUSEADDR: int = 2
SO_KEEPALIVE: int = 9
SO_LINGER: int = 13
SO_RCVBUF: int = 8
SO_SNDBUF: int = 7
TCP_NODELAY: int = 1
IPPROTO_IPV6: int = 41
IPV6_V6ONLY: int = 26

# Shutdown flags
SHUT_RD: int = 0
SHUT_WR: int = 1
SHUT_RDWR: int = 2

# Errors
error: int = 0
timeout: int = 0
herror: int = 0
gaierror: int = 0

# Name resolution constants
AI_PASSIVE: int = 1
AI_CANONNAME: int = 2
AI_NUMERICHOST: int = 4
NI_NUMERICHOST: int = 1
NI_NUMERICSERV: int = 2

# Special address strings
INADDR_ANY: str = "0.0.0.0"
INADDR_BROADCAST: str = "255.255.255.255"
INADDR_LOOPBACK: str = "127.0.0.1"


def gethostname() -> str:
    """Return the hostname of the machine."""
    return "localhost"


def gethostbyname(hostname: str) -> str:
    """Translate hostname to IP address string (stub)."""
    if hostname == "localhost":
        return "127.0.0.1"
    return "0.0.0.0"


def getfqdn(name: str = "") -> str:
    """Return fully qualified domain name (stub)."""
    return "localhost"


def getaddrinfo(host: str, port: int, family: int = 0,
                socktype: int = 0, proto: int = 0, flags: int = 0) -> list:
    """Return address information (stub, returns empty list)."""
    return []


def getnameinfo(sockaddr: list, flags: int) -> list:
    """Return (host, port) for a socket address (stub)."""
    return ["localhost", "0"]


def inet_aton(ip_string: str) -> str:
    """Convert dotted-quad IP string to 4-byte packed form (stub)."""
    return ip_string


def inet_ntoa(packed_ip: str) -> str:
    """Convert 4-byte packed IP to dotted-quad string (stub)."""
    return "0.0.0.0"


def setdefaulttimeout(timeout: float) -> None:
    """Set the default socket timeout (stub, no-op)."""
    pass


def getdefaulttimeout() -> float:
    """Get the default socket timeout (stub, returns 0)."""
    return 0.0


def htons(x: int) -> int:
    """Convert 16-bit int from host to network byte order."""
    lo: int = x & 0xFF
    hi: int = (x >> 8) & 0xFF
    return (lo << 8) | hi


def htonl(x: int) -> int:
    """Convert 32-bit int from host to network byte order."""
    b0: int = x & 0xFF
    b1: int = (x >> 8) & 0xFF
    b2: int = (x >> 16) & 0xFF
    b3: int = (x >> 24) & 0xFF
    return (b0 << 24) | (b1 << 16) | (b2 << 8) | b3


def ntohs(x: int) -> int:
    """Convert 16-bit int from network to host byte order."""
    return htons(x)


def ntohl(x: int) -> int:
    """Convert 32-bit int from network to host byte order."""
    return htonl(x)


class Socket:
    """A network socket stub."""

    def __init__(self, family: int = 2, socktype: int = 1, proto: int = 0) -> None:
        self.family: int = family
        self.type: int = socktype
        self.proto: int = proto
        self._fd: int = -1
        self._timeout: float = 0.0

    def bind(self, address: list) -> int:
        """Bind socket to address (stub, returns 0)."""
        return 0

    def listen(self, backlog: int = 5) -> int:
        """Enable a server to accept connections (stub)."""
        return 0

    def connect(self, address: list) -> int:
        """Connect to a remote socket (stub)."""
        return 0

    def accept(self) -> list:
        """Accept a connection (stub, returns [0, 0])."""
        return [0, 0]

    def send(self, data: str) -> int:
        """Send data (stub, returns 0)."""
        return 0

    def recv(self, bufsize: int) -> str:
        """Receive data (stub, returns empty string)."""
        return ""

    def sendto(self, data: str, address: list) -> int:
        """Send data to address (stub)."""
        return 0

    def recvfrom(self, bufsize: int) -> list:
        """Receive data and sender's address (stub)."""
        return ["", ""]

    def close(self) -> int:
        """Close the socket (stub)."""
        return 0

    def shutdown(self, how: int) -> int:
        """Shut down one or both halves of the connection (stub)."""
        return 0

    def setsockopt(self, level: int, optname: int, value: int) -> int:
        """Set a socket option (stub)."""
        return 0

    def getsockopt(self, level: int, optname: int, buflen: int = 0) -> int:
        """Get a socket option (stub, returns 0)."""
        return 0

    def settimeout(self, timeout: float) -> None:
        """Set the socket timeout (stub)."""
        self._timeout = timeout

    def gettimeout(self) -> float:
        """Get the socket timeout (stub)."""
        return self._timeout

    def setblocking(self, flag: int) -> None:
        """Set blocking mode (stub)."""
        pass

    def fileno(self) -> int:
        """Return file descriptor (stub, returns -1)."""
        return self._fd

    def getsockname(self) -> list:
        """Return socket's own address (stub)."""
        return ["0.0.0.0", "0"]

    def getpeername(self) -> list:
        """Return remote address (stub)."""
        return ["0.0.0.0", "0"]

    def makefile(self, mode: str = "r") -> str:
        """Return a file object for the socket (stub)."""
        return ""

    def __enter__(self) -> socket:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


def create_connection(address: list, timeout: int = 0) -> Socket:
    """Convenience function to create a connected socket (stub)."""
    return Socket()


def create_server(address: list, family: int = 2, backlog: int = 5) -> Socket:
    """Create a TCP server socket (stub)."""
    return Socket(family)
