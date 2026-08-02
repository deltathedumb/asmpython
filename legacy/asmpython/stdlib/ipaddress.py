"""ipaddress module: IPv4 and IPv6 address handling."""
from __future__ import annotations


def _parse_ipv4(address: str) -> list[int]:
    parts: list[str] = address.split(".")
    if len(parts) != 4:
        raise ValueError("Invalid IPv4 address: " + address)
    result: list[int] = []
    for p in parts:
        n: int = int(p)
        if n < 0 or n > 255:
            raise ValueError("Invalid octet: " + p)
        result.append(n)
    return result


def _ipv4_to_int(octets: list[int]) -> int:
    return (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]


def _int_to_ipv4(n: int) -> str:
    a: int = (n >> 24) & 0xFF
    b: int = (n >> 16) & 0xFF
    c: int = (n >> 8) & 0xFF
    d: int = n & 0xFF
    return str(a) + "." + str(b) + "." + str(c) + "." + str(d)


class IPv4Address:
    """An IPv4 address."""

    def __init__(self, address: str) -> None:
        if "/" in address:
            raise ValueError("Use IPv4Network for addresses with prefix length")
        self._octets: list[int] = _parse_ipv4(address)
        self._int: int = _ipv4_to_int(self._octets)

    def __str__(self) -> str:
        return _int_to_ipv4(self._int)

    def __repr__(self) -> str:
        return "IPv4Address('" + str(self) + "')"

    def __eq__(self, other: "IPv4Address") -> int:
        return 1 if self._int == other._int else 0

    def __lt__(self, other: "IPv4Address") -> int:
        return 1 if self._int < other._int else 0

    def __le__(self, other: "IPv4Address") -> int:
        return 1 if self._int <= other._int else 0

    def __gt__(self, other: "IPv4Address") -> int:
        return 1 if self._int > other._int else 0

    def __ge__(self, other: "IPv4Address") -> int:
        return 1 if self._int >= other._int else 0

    def __hash__(self) -> int:
        return self._int

    @property
    def packed(self) -> str:
        return (chr(self._octets[0]) + chr(self._octets[1]) +
                chr(self._octets[2]) + chr(self._octets[3]))

    @property
    def is_private(self) -> int:
        n: int = self._int
        if (n >> 24) == 10:
            return 1
        if (n >> 20) == (172 << 4 | 1):
            return 1
        if (n >> 16) == (192 << 8 | 168):
            return 1
        return 0

    @property
    def is_loopback(self) -> int:
        return 1 if (self._int >> 24) == 127 else 0

    @property
    def is_multicast(self) -> int:
        first: int = self._int >> 24
        return 1 if first >= 224 and first <= 239 else 0

    @property
    def is_link_local(self) -> int:
        return 1 if (self._int >> 16) == (169 << 8 | 254) else 0

    @property
    def is_unspecified(self) -> int:
        return 1 if self._int == 0 else 0

    @property
    def is_broadcast(self) -> int:
        return 1 if self._int == 0xFFFFFFFF else 0

    @property
    def version(self) -> int:
        return 4

    @property
    def max_prefixlen(self) -> int:
        return 32

    @property
    def reverse_pointer(self) -> str:
        o: list[int] = self._octets
        return (str(o[3]) + "." + str(o[2]) + "." + str(o[1]) + "." +
                str(o[0]) + ".in-addr.arpa")


class IPv4Network:
    """An IPv4 network."""

    def __init__(self, address: str, strict: int = 1) -> None:
        if "/" in address:
            parts: list[str] = address.split("/")
            host_str: str = parts[0]
            prefix_str: str = parts[1]
            self.prefixlen: int = int(prefix_str)
        else:
            host_str = address
            self.prefixlen = 32
        octets: list[int] = _parse_ipv4(host_str)
        host_int: int = _ipv4_to_int(octets)
        mask: int = (0xFFFFFFFF << (32 - self.prefixlen)) & 0xFFFFFFFF
        network_int: int = host_int & mask
        if strict and network_int != host_int:
            raise ValueError("host bits set in " + address)
        self._network_int: int = network_int
        self._mask: int = mask

    @property
    def network_address(self) -> IPv4Address:
        return IPv4Address(_int_to_ipv4(self._network_int))

    @property
    def broadcast_address(self) -> IPv4Address:
        return IPv4Address(_int_to_ipv4(self._network_int | (~self._mask & 0xFFFFFFFF)))

    @property
    def netmask(self) -> IPv4Address:
        return IPv4Address(_int_to_ipv4(self._mask))

    @property
    def num_addresses(self) -> int:
        return 1 << (32 - self.prefixlen)

    @property
    def version(self) -> int:
        return 4

    @property
    def max_prefixlen(self) -> int:
        return 32

    def overlaps(self, other: "IPv4Network") -> int:
        n1_start: int = self._network_int
        n1_end: int = self._network_int | (~self._mask & 0xFFFFFFFF)
        n2_start: int = other._network_int
        n2_end: int = other._network_int | (~other._mask & 0xFFFFFFFF)
        if n1_start <= n2_end and n2_start <= n1_end:
            return 1
        return 0

    def supernet_of(self, other: "IPv4Network") -> int:
        if other.prefixlen < self.prefixlen:
            return 0
        return 1 if (other._network_int & self._mask) == self._network_int else 0

    def subnet_of(self, other: "IPv4Network") -> int:
        return other.supernet_of(self)

    def __str__(self) -> str:
        return _int_to_ipv4(self._network_int) + "/" + str(self.prefixlen)

    def __repr__(self) -> str:
        return "IPv4Network('" + str(self) + "')"

    def __contains__(self, address: IPv4Address) -> int:
        return 1 if (address._int & self._mask) == self._network_int else 0


class IPv4Interface(IPv4Address):
    """An IPv4 interface (address with network prefix)."""

    def __init__(self, address: str) -> None:
        if "/" in address:
            parts: list[str] = address.split("/")
            host_str: str = parts[0]
            self.prefixlen: int = int(parts[1])
        else:
            host_str = address
            self.prefixlen = 32
        super().__init__(host_str)
        mask: int = (0xFFFFFFFF << (32 - self.prefixlen)) & 0xFFFFFFFF
        self._mask: int = mask
        self._network_int: int = self._int & mask

    @property
    def network(self) -> IPv4Network:
        return IPv4Network(_int_to_ipv4(self._network_int) + "/" + str(self.prefixlen), strict=0)

    @property
    def ip(self) -> IPv4Address:
        return IPv4Address(_int_to_ipv4(self._int))

    def __str__(self) -> str:
        return _int_to_ipv4(self._int) + "/" + str(self.prefixlen)


def ip_address(address: str) -> IPv4Address:
    """Return an IPv4Address object."""
    return IPv4Address(address)


def ip_network(address: str, strict: int = 1) -> IPv4Network:
    """Return an IPv4Network object."""
    return IPv4Network(address, strict)


def ip_interface(address: str) -> IPv4Interface:
    """Return an IPv4Interface object."""
    return IPv4Interface(address)


def summarize_address_range(first: IPv4Address, last: IPv4Address) -> list[str]:
    """Summarize a network range given first and last IP."""
    first_int: int = first._int
    last_int: int = last._int
    result: list[str] = []
    while first_int <= last_int:
        prefix: int = 32
        while prefix > 0:
            mask: int = (0xFFFFFFFF << (32 - (prefix - 1))) & 0xFFFFFFFF
            if (first_int & mask) == first_int:
                block_size: int = 1 << (32 - (prefix - 1))
                if first_int + block_size - 1 <= last_int:
                    prefix = prefix - 1
                else:
                    break
            else:
                break
        result.append(_int_to_ipv4(first_int) + "/" + str(prefix))
        first_int = first_int + (1 << (32 - prefix))
    return result


def collapse_addresses(addresses: list[str]) -> list[str]:
    """Collapse a list of IP addresses/networks."""
    return addresses
