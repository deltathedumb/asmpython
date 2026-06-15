"""hashlib module: secure hash and message digest algorithms.

Pure-Python implementation of MD5 and SHA-256 that compiles to native code.
This works on data represented as list[int] (bytes).
"""
from __future__ import annotations


def _rotr32(x: int, n: int) -> int:
    """Rotate right 32-bit integer x by n bits."""
    x = x & 0xFFFFFFFF
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def _sigma0(x: int) -> int:
    return _rotr32(x, 2) ^ _rotr32(x, 13) ^ _rotr32(x, 22)


def _sigma1(x: int) -> int:
    return _rotr32(x, 6) ^ _rotr32(x, 11) ^ _rotr32(x, 25)


def _gamma0(x: int) -> int:
    return _rotr32(x, 7) ^ _rotr32(x, 18) ^ (x >> 3)


def _gamma1(x: int) -> int:
    return _rotr32(x, 17) ^ _rotr32(x, 19) ^ (x >> 10)


_SHA256_K: list = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5,
    0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc,
    0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3,
    0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5,
    0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]


def _sha256_process_block(state: list, block: list) -> None:
    """Process a 64-byte block and update state in-place."""
    w: list = []
    i: int = 0
    while i < 16:
        w.append((block[i*4] << 24) | (block[i*4+1] << 16) |
                 (block[i*4+2] << 8) | block[i*4+3])
        i = i + 1
    while i < 64:
        w.append((_gamma1(w[i-2]) + w[i-7] + _gamma0(w[i-15]) + w[i-16]) & 0xFFFFFFFF)
        i = i + 1

    a: int = state[0]
    b: int = state[1]
    c: int = state[2]
    d: int = state[3]
    e: int = state[4]
    f: int = state[5]
    g: int = state[6]
    h: int = state[7]

    i = 0
    while i < 64:
        t1: int = (h + _sigma1(e) + ((e & f) ^ (~e & g)) + _SHA256_K[i] + w[i]) & 0xFFFFFFFF
        t2: int = (_sigma0(a) + ((a & b) ^ (a & c) ^ (b & c))) & 0xFFFFFFFF
        h = g
        g = f
        f = e
        e = (d + t1) & 0xFFFFFFFF
        d = c
        c = b
        b = a
        a = (t1 + t2) & 0xFFFFFFFF
        i = i + 1

    state[0] = (state[0] + a) & 0xFFFFFFFF
    state[1] = (state[1] + b) & 0xFFFFFFFF
    state[2] = (state[2] + c) & 0xFFFFFFFF
    state[3] = (state[3] + d) & 0xFFFFFFFF
    state[4] = (state[4] + e) & 0xFFFFFFFF
    state[5] = (state[5] + f) & 0xFFFFFFFF
    state[6] = (state[6] + g) & 0xFFFFFFFF
    state[7] = (state[7] + h) & 0xFFFFFFFF


def _bytes_to_hex(data: list) -> str:
    """Convert list of byte ints to hex string."""
    hex_chars: str = "0123456789abcdef"
    result: str = ""
    i: int = 0
    while i < len(data):
        b: int = int(data[i]) & 0xFF
        result = result + hex_chars[b >> 4] + hex_chars[b & 0xF]
        i = i + 1
    return result


def _str_to_bytes(s: str) -> list:
    """Convert string to list of byte ints (UTF-8 ASCII subset)."""
    result: list = []
    i: int = 0
    while i < len(s):
        c: int = ord(s[i])
        if c < 128:
            result.append(c)
        elif c < 2048:
            result.append(0xC0 | (c >> 6))
            result.append(0x80 | (c & 0x3F))
        else:
            result.append(0xE0 | (c >> 12))
            result.append(0x80 | ((c >> 6) & 0x3F))
            result.append(0x80 | (c & 0x3F))
        i = i + 1
    return result


class sha256:
    """SHA-256 hash object."""

    def __init__(self) -> None:
        self._state: list = [
            0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
            0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
        ]
        self._buf: list = []
        self._length: int = 0
        self.digest_size: int = 32
        self.block_size: int = 64
        self.name: str = "sha256"

    def update(self, data: list) -> None:
        """Feed data (list of bytes) into the hash."""
        i: int = 0
        while i < len(data):
            self._buf.append(data[i])
            self._length = self._length + 1
            if len(self._buf) == 64:
                _sha256_process_block(self._state, self._buf)
                self._buf = []
            i = i + 1

    def update_str(self, s: str) -> None:
        """Feed a string (encoded as UTF-8) into the hash."""
        self.update(_str_to_bytes(s))

    def digest(self) -> list:
        """Return the hash as a list of 32 bytes."""
        saved_state: list = [
            self._state[0], self._state[1], self._state[2], self._state[3],
            self._state[4], self._state[5], self._state[6], self._state[7],
        ]
        saved_buf: list = []
        i: int = 0
        while i < len(self._buf):
            saved_buf.append(self._buf[i])
            i = i + 1
        saved_length: int = self._length

        padded: list = []
        i = 0
        while i < len(self._buf):
            padded.append(self._buf[i])
            i = i + 1
        padded.append(0x80)
        while len(padded) % 64 != 56:
            padded.append(0)
        bit_len: int = self._length * 8
        padded.append((bit_len >> 56) & 0xFF)
        padded.append((bit_len >> 48) & 0xFF)
        padded.append((bit_len >> 40) & 0xFF)
        padded.append((bit_len >> 32) & 0xFF)
        padded.append((bit_len >> 24) & 0xFF)
        padded.append((bit_len >> 16) & 0xFF)
        padded.append((bit_len >> 8) & 0xFF)
        padded.append(bit_len & 0xFF)

        j: int = 0
        while j < len(padded):
            block: list = []
            k: int = 0
            while k < 64:
                block.append(padded[j + k])
                k = k + 1
            _sha256_process_block(self._state, block)
            j = j + 64

        result: list = []
        i = 0
        while i < 8:
            v: int = self._state[i]
            result.append((v >> 24) & 0xFF)
            result.append((v >> 16) & 0xFF)
            result.append((v >> 8) & 0xFF)
            result.append(v & 0xFF)
            i = i + 1

        self._state = saved_state
        self._buf = saved_buf
        self._length = saved_length
        return result

    def hexdigest(self) -> str:
        """Return the hash as a hex string."""
        return _bytes_to_hex(self.digest())

    def copy(self) -> sha256:
        """Return a copy of the hash object."""
        h: sha256 = sha256()
        h._state = [
            self._state[0], self._state[1], self._state[2], self._state[3],
            self._state[4], self._state[5], self._state[6], self._state[7],
        ]
        i: int = 0
        while i < len(self._buf):
            h._buf.append(self._buf[i])
            i = i + 1
        h._length = self._length
        return h


def _md5_rotl32(x: int, n: int) -> int:
    x = x & 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


_MD5_S: list = [
    7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
    5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20, 5,  9, 14, 20,
    4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
    6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
]

_MD5_K: list = [
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee,
    0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa,
    0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
    0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
    0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05,
    0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039,
    0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
    0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
]


def _md5_process_block(state: list, block: list) -> None:
    """Process a 64-byte block updating state in-place."""
    m: list = []
    i: int = 0
    while i < 16:
        m.append(block[i*4] | (block[i*4+1] << 8) |
                 (block[i*4+2] << 16) | (block[i*4+3] << 24))
        i = i + 1

    a: int = state[0]
    b: int = state[1]
    c: int = state[2]
    d: int = state[3]

    i = 0
    while i < 64:
        if i < 16:
            f: int = (b & c) | (~b & d)
            g: int = i
        elif i < 32:
            f = (d & b) | (~d & c)
            g = (5 * i + 1) % 16
        elif i < 48:
            f = b ^ c ^ d
            g = (3 * i + 5) % 16
        else:
            f = c ^ (b | ~d)
            g = (7 * i) % 16
        f = (f + a + _MD5_K[i] + m[g]) & 0xFFFFFFFF
        a = d
        d = c
        c = b
        b = (b + _md5_rotl32(f, _MD5_S[i])) & 0xFFFFFFFF
        i = i + 1

    state[0] = (state[0] + a) & 0xFFFFFFFF
    state[1] = (state[1] + b) & 0xFFFFFFFF
    state[2] = (state[2] + c) & 0xFFFFFFFF
    state[3] = (state[3] + d) & 0xFFFFFFFF


class md5:
    """MD5 hash object."""

    def __init__(self) -> None:
        self._state: list = [0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476]
        self._buf: list = []
        self._length: int = 0
        self.digest_size: int = 16
        self.block_size: int = 64
        self.name: str = "md5"

    def update(self, data: list) -> None:
        i: int = 0
        while i < len(data):
            self._buf.append(data[i])
            self._length = self._length + 1
            if len(self._buf) == 64:
                _md5_process_block(self._state, self._buf)
                self._buf = []
            i = i + 1

    def update_str(self, s: str) -> None:
        self.update(_str_to_bytes(s))

    def digest(self) -> list:
        saved_state: list = [self._state[0], self._state[1], self._state[2], self._state[3]]
        saved_buf: list = []
        i: int = 0
        while i < len(self._buf):
            saved_buf.append(self._buf[i])
            i = i + 1
        saved_length: int = self._length

        padded: list = []
        i = 0
        while i < len(self._buf):
            padded.append(self._buf[i])
            i = i + 1
        padded.append(0x80)
        while len(padded) % 64 != 56:
            padded.append(0)
        bit_len: int = self._length * 8
        padded.append(bit_len & 0xFF)
        padded.append((bit_len >> 8) & 0xFF)
        padded.append((bit_len >> 16) & 0xFF)
        padded.append((bit_len >> 24) & 0xFF)
        padded.append((bit_len >> 32) & 0xFF)
        padded.append((bit_len >> 40) & 0xFF)
        padded.append((bit_len >> 48) & 0xFF)
        padded.append((bit_len >> 56) & 0xFF)

        j: int = 0
        while j < len(padded):
            block: list = []
            k: int = 0
            while k < 64:
                block.append(padded[j + k])
                k = k + 1
            _md5_process_block(self._state, block)
            j = j + 64

        result: list = []
        i = 0
        while i < 4:
            v: int = self._state[i]
            result.append(v & 0xFF)
            result.append((v >> 8) & 0xFF)
            result.append((v >> 16) & 0xFF)
            result.append((v >> 24) & 0xFF)
            i = i + 1

        self._state = saved_state
        self._buf = saved_buf
        self._length = saved_length
        return result

    def hexdigest(self) -> str:
        return _bytes_to_hex(self.digest())

    def copy(self) -> md5:
        h: md5 = md5()
        h._state = [self._state[0], self._state[1], self._state[2], self._state[3]]
        i: int = 0
        while i < len(self._buf):
            h._buf.append(self._buf[i])
            i = i + 1
        h._length = self._length
        return h


def new(name: str) -> sha256:
    """Create a new hash object by name ('sha256' or 'md5')."""
    if name == "sha256" or name == "SHA256" or name == "sha-256":
        return sha256()
    return md5()
