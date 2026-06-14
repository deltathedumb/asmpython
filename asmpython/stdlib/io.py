"""io module: in-memory stream classes.

Implements StringIO as a compiled Python class so it works as a native
asmpython type. BytesIO is omitted (asmpython has no bytes type).
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
