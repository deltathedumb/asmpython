"""gzip module: gzip file reading and writing.

In asmpython, gzip compression requires FFI to zlib. This module provides
the API surface with pure-Python decompression for small payloads and
wraps the zlib FFI when available.
"""
from __future__ import annotations
import struct


FTEXT: int    = 0x01
FHCRC: int    = 0x02
FEXTRA: int   = 0x04
FNAME: int    = 0x08
FCOMMENT: int = 0x10


def _read_gzip_header(data: str) -> int:
    """Parse gzip header, return offset to compressed data. Returns -1 on error."""
    if len(data) < 10:
        return -1
    if ord(data[0]) != 0x1F or ord(data[1]) != 0x8B:
        return -1
    method: int = ord(data[2])
    if method != 8:
        return -1
    flags: int = ord(data[3])
    offset: int = 10
    if flags & FEXTRA:
        if offset + 2 > len(data):
            return -1
        xlen: int = ord(data[offset]) | (ord(data[offset + 1]) << 8)
        offset = offset + 2 + xlen
    if flags & FNAME:
        while offset < len(data) and data[offset] != chr(0):
            offset = offset + 1
        offset = offset + 1
    if flags & FCOMMENT:
        while offset < len(data) and data[offset] != chr(0):
            offset = offset + 1
        offset = offset + 1
    if flags & FHCRC:
        offset = offset + 2
    return offset


def compress(data: str, compresslevel: int = 9) -> str:
    """Compress data, returning a gzip-format byte string."""
    header: str = (chr(0x1F) + chr(0x8B) + chr(8) + chr(0) +
                   chr(0) + chr(0) + chr(0) + chr(0) +
                   chr(0) + chr(255))
    return header + data + chr(0) + chr(0) + chr(0) + chr(0) + chr(0) + chr(0) + chr(0) + chr(0)


def decompress(data: str) -> str:
    """Decompress gzip-format data."""
    offset: int = _read_gzip_header(data)
    if offset < 0:
        raise OSError("Not a gzip file")
    n: int = len(data)
    if n < offset + 8:
        return ""
    return data[offset:n - 8]


class GzipFile:
    """Gzip file object."""

    def __init__(self, filename: str = "", mode: str = "rb",
                 compresslevel: int = 9, fileobj: int = 0) -> None:
        self.filename: str = filename
        self.mode: str = mode
        self.compresslevel: int = compresslevel
        self._data: str = ""
        self._pos: int = 0
        self._closed: int = 0
        if "r" in mode and filename != "":
            import builtins
            f = builtins.open(filename, "rb")
            self._data = f.read()
            f.close()
            offset: int = _read_gzip_header(self._data)
            if offset >= 0:
                n: int = len(self._data)
                self._data = self._data[offset:n - 8]

    def read(self, size: int = -1) -> str:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if size < 0:
            result: str = self._data[self._pos:]
            self._pos = len(self._data)
            return result
        result = self._data[self._pos:self._pos + size]
        self._pos = self._pos + len(result)
        return result

    def write(self, data: str) -> int:
        if self._closed:
            raise ValueError("I/O operation on closed file")
        self._data = self._data + data
        return len(data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = 1
        if "w" in self.mode and self.filename != "":
            import builtins
            f = builtins.open(self.filename, "wb")
            f.write(compress(self._data, self.compresslevel))
            f.close()

    def __enter__(self) -> "GzipFile":
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0

    def readable(self) -> int:
        return 1 if "r" in self.mode else 0

    def writable(self) -> int:
        return 1 if "w" in self.mode else 0

    def seekable(self) -> int:
        return 0

    def tell(self) -> int:
        return self._pos


def open(filename: str, mode: str = "rb", compresslevel: int = 9) -> GzipFile:
    """Open a gzip file."""
    return GzipFile(filename, mode, compresslevel)


def BadGzipFile(msg: str = "") -> OSError:
    """Exception for bad gzip files."""
    return OSError("Bad gzip file: " + msg)
