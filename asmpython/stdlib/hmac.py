"""hmac module: HMAC-based message authentication (RFC 2104)."""
from __future__ import annotations
import hashlib


def _xor_bytes(a: str, b: str) -> str:
    result: str = ""
    i: int = 0
    n: int = len(a)
    while i < n:
        result = result + chr(ord(a[i]) ^ ord(b[i]))
        i = i + 1
    return result


def _pad(key: str, block_size: int, fill: int) -> str:
    result: str = key
    while len(result) < block_size:
        result = result + chr(fill)
    return result


def new(key: str, msg: str = "", digestmod: str = "sha256") -> "HMAC":
    """Create a new HMAC object."""
    h: HMAC = HMAC(key, digestmod)
    if msg != "":
        h.update(msg)
    return h


class HMAC:
    """HMAC authenticator."""

    def __init__(self, key: str, digestmod: str = "sha256") -> None:
        self._digestmod: str = digestmod
        self._msg: str = ""
        if digestmod == "sha256" or digestmod == "sha-256":
            block_size: int = 64
        elif digestmod == "sha512" or digestmod == "sha-512":
            block_size = 128
        elif digestmod == "sha1" or digestmod == "sha-1":
            block_size = 64
        elif digestmod == "md5":
            block_size = 64
        else:
            block_size = 64
        self._block_size: int = block_size
        if len(key) > block_size:
            key = hashlib.new(digestmod, key).hexdigest()
        while len(key) < block_size:
            key = key + chr(0)
        self._key: str = key
        self._ipad: str = _xor_bytes(key, _pad("", block_size, 0x36))
        self._opad: str = _xor_bytes(key, _pad("", block_size, 0x5c))

    def update(self, msg: str) -> None:
        self._msg = self._msg + msg

    def digest(self) -> str:
        inner: str = hashlib.new(self._digestmod, self._ipad + self._msg).hexdigest()
        outer: str = hashlib.new(self._digestmod, self._opad + inner).hexdigest()
        return outer

    def hexdigest(self) -> str:
        return self.digest()

    def copy(self) -> "HMAC":
        h: HMAC = HMAC(self._key, self._digestmod)
        h._msg = self._msg
        return h

    @property
    def digest_size(self) -> int:
        if self._digestmod == "sha256" or self._digestmod == "sha-256":
            return 32
        elif self._digestmod == "sha512" or self._digestmod == "sha-512":
            return 64
        elif self._digestmod == "sha1" or self._digestmod == "sha-1":
            return 20
        elif self._digestmod == "md5":
            return 16
        return 32

    @property
    def name(self) -> str:
        return "hmac-" + self._digestmod


def digest(key: str, msg: str, digest: str) -> str:
    """Single-call HMAC digest."""
    h: HMAC = new(key, msg, digest)
    return h.hexdigest()


def compare_digest(a: str, b: str) -> int:
    """Constant-time string comparison to prevent timing attacks."""
    if len(a) != len(b):
        return 0
    result: int = 0
    i: int = 0
    while i < len(a):
        if a[i] != b[i]:
            result = 1
        i = i + 1
    return 1 if result == 0 else 0
