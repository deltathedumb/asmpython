"""io module: in-memory stream classes.

Implements StringIO and BytesIO (as list[int]) as compiled Python classes
so they work as native asmpython types.
"""
from __future__ import annotations


class StringIO:
    """In-memory text stream."""

    def __init__(self, initial: str = "") -> None:
        self._buf: str = initial
        self._pos: int = 0

    def write(self, s: str) -> int:
        self._buf = self._buf + s
        return len(s)

    def read(self, n: int = -1) -> str:
        if n < 0:
            result: str = self._buf[self._pos:]
            self._pos = len(self._buf)
            return result
        end: int = self._pos + n
        if end > len(self._buf):
            end = len(self._buf)
        result2: str = self._buf[self._pos:end]
        self._pos = end
        return result2

    def readline(self) -> str:
        start: int = self._pos
        i: int = self._pos
        n: int = len(self._buf)
        while i < n and self._buf[i] != "\n":
            i = i + 1
        if i < n:
            i = i + 1  # include the newline
        self._pos = i
        return self._buf[start:i]

    def readlines(self) -> list:
        lines: list[str] = []
        while self._pos < len(self._buf):
            lines.append(self.readline())
        return lines

    def seek(self, pos: int) -> int:
        if pos < 0:
            pos = 0
        if pos > len(self._buf):
            pos = len(self._buf)
        self._pos = pos
        return self._pos

    def tell(self) -> int:
        return self._pos

    def getvalue(self) -> str:
        return self._buf

    def truncate(self, size: int = -1) -> int:
        if size < 0:
            size = self._pos
        self._buf = self._buf[:size]
        if self._pos > size:
            self._pos = size
        return size

    def close(self) -> None:
        pass

    def __str__(self) -> str:
        return self._buf


class BytesIO:
    """In-memory binary stream backed by list[int] (one int per byte)."""

    def __init__(self, initial: list = []) -> None:
        self._buf: list = []
        self._pos: int = 0
        i: int = 0
        while i < len(initial):
            self._buf.append(initial[i])
            i = i + 1

    def write(self, data: list) -> int:
        i: int = 0
        while i < len(data):
            self._buf.append(data[i])
            i = i + 1
        return len(data)

    def write_byte(self, b: int) -> int:
        self._buf.append(b & 0xFF)
        return 1

    def read(self, n: int = -1) -> list:
        result: list = []
        if n < 0:
            i: int = self._pos
            while i < len(self._buf):
                result.append(self._buf[i])
                i = i + 1
            self._pos = len(self._buf)
            return result
        end: int = self._pos + n
        if end > len(self._buf):
            end = len(self._buf)
        i = self._pos
        while i < end:
            result.append(self._buf[i])
            i = i + 1
        self._pos = end
        return result

    def read1(self) -> int:
        """Read a single byte as int, or -1 at EOF."""
        if self._pos >= len(self._buf):
            return -1
        b: int = self._buf[self._pos]
        self._pos = self._pos + 1
        return b

    def seek(self, pos: int) -> int:
        if pos < 0:
            pos = 0
        if pos > len(self._buf):
            pos = len(self._buf)
        self._pos = pos
        return self._pos

    def tell(self) -> int:
        return self._pos

    def getvalue(self) -> list:
        result: list = []
        i: int = 0
        while i < len(self._buf):
            result.append(self._buf[i])
            i = i + 1
        return result

    def truncate(self, size: int = -1) -> int:
        if size < 0:
            size = self._pos
        new_buf: list = []
        i: int = 0
        while i < size and i < len(self._buf):
            new_buf.append(self._buf[i])
            i = i + 1
        self._buf = new_buf
        if self._pos > size:
            self._pos = size
        return size

    def close(self) -> None:
        pass

    def __len__(self) -> int:
        return len(self._buf)


class RawIOBase:
    """Abstract base for raw binary I/O (stub)."""

    def read(self, n: int = -1) -> list:
        return []

    def readall(self) -> list:
        return []

    def write(self, data: list) -> int:
        return 0

    def close(self) -> None:
        pass


class UnsupportedOperation(Exception):
    """Raised when an I/O operation is not supported (stub)."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "UnsupportedOperation: " + self.msg


DEFAULT_BUFFER_SIZE: int = 8192
