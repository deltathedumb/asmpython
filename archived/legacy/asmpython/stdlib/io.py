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

    def readable(self) -> int:
        return 1

    def writable(self) -> int:
        return 1

    def seekable(self) -> int:
        return 1

    def __enter__(self) -> StringIO:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0

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

    def readable(self) -> int:
        return 1

    def writable(self) -> int:
        return 1

    def seekable(self) -> int:
        return 1

    def __enter__(self) -> BytesIO:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0

    def __len__(self) -> int:
        return len(self._buf)


class UnsupportedOperation(Exception):
    """Raised when an I/O operation is not supported (stub)."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "UnsupportedOperation: " + self.msg


DEFAULT_BUFFER_SIZE: int = 8192


class RawIOBase:
    """Base class for raw binary I/O (stub)."""

    def read(self, size: int = -1) -> str:
        return ""

    def readinto(self, b: list) -> int:
        return 0

    def write(self, b: str) -> int:
        return 0

    def readable(self) -> int:
        return 0

    def writable(self) -> int:
        return 0

    def seekable(self) -> int:
        return 0


class FileIO(RawIOBase):
    """Raw file I/O backed by C stdio FILE*."""

    def __init__(self, name: str, mode: str = "r", closefd: int = 1) -> None:
        import os
        self._name: str = name
        self._mode: str = mode
        self._fp: str = os.fopen(name, mode)
        self._closed: int = 0

    def read(self, size: int = -1) -> str:
        import os
        if self._closed:
            return ""
        result: str = ""
        c: int = os.fgetc(self._fp)
        count: int = 0
        while c != -1:
            result = result + chr(c)
            count = count + 1
            if size > 0 and count >= size:
                break
            c = os.fgetc(self._fp)
        return result

    def read_bytes(self, size: int = -1) -> list:
        """Like read(), but returns raw bytes as list[int] (0..255) instead
        of a str -- for binary formats (images, .glb/.bin glTF buffers,
        ...) where round-tripping through chr()/str risks the string
        machinery treating the data as encoded text instead of opaque
        bytes. fgetc() already returns one raw byte (0..255) or -1 at EOF,
        so this just collects those directly with no str involved at all."""
        import os
        result: list = []
        if self._closed:
            return result
        c: int = os.fgetc(self._fp)
        count: int = 0
        while c != -1:
            result.append(c)
            count = count + 1
            if size > 0 and count >= size:
                break
            c = os.fgetc(self._fp)
        return result

    def write(self, b: str) -> int:
        import os
        if self._closed:
            return 0
        return os.fputs(b, self._fp)

    def close(self) -> None:
        import os
        if not self._closed:
            os.fclose(self._fp)
            self._closed = 1

    def readable(self) -> int:
        return 1 if "r" in self._mode else 0

    def writable(self) -> int:
        return 1 if "w" in self._mode or "a" in self._mode else 0

    def seekable(self) -> int:
        return 1

    def seek(self, pos: int, whence: int = 0) -> int:
        import os
        return os.fseek(self._fp, pos, whence)

    def tell(self) -> int:
        import os
        return os.ftell(self._fp)

    def __enter__(self) -> FileIO:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


class BufferedReader:
    """Buffered binary reader wrapping a RawIOBase (stub: reads via read())."""

    def __init__(self, raw: FileIO, buffer_size: int = 8192) -> None:
        self._raw: FileIO = raw
        self._buf: str = ""
        self._pos: int = 0

    def read(self, size: int = -1) -> str:
        return self._raw.read(size)

    def readline(self) -> str:
        result: str = ""
        c: str = self._raw.read(1)
        while c != "" and c != "\n":
            result = result + c
            c = self._raw.read(1)
        if c == "\n":
            result = result + "\n"
        return result

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> BufferedReader:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


class BufferedWriter:
    """Buffered binary writer wrapping a RawIOBase."""

    def __init__(self, raw: FileIO, buffer_size: int = 8192) -> None:
        self._raw: FileIO = raw

    def write(self, b: str) -> int:
        return self._raw.write(b)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self._raw.close()

    def __enter__(self) -> BufferedWriter:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


class TextIOWrapper:
    """Text mode wrapper around a binary stream."""

    def __init__(self, buffer: FileIO, encoding: str = "utf-8",
                 errors: str = "strict", newline: str = "") -> None:
        self._buf: FileIO = buffer
        self.encoding: str = encoding

    def read(self, size: int = -1) -> str:
        return self._buf.read(size)

    def readline(self) -> str:
        result: str = ""
        c: str = self._buf.read(1)
        while c != "" and c != "\n":
            result = result + c
            c = self._buf.read(1)
        if c == "\n":
            result = result + "\n"
        return result

    def write(self, s: str) -> int:
        return self._buf.write(s)

    def close(self) -> None:
        self._buf.close()

    def flush(self) -> None:
        pass

    def __enter__(self) -> TextIOWrapper:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


def text_open(file: str, mode: str = "r", encoding: str = "utf-8") -> TextIOWrapper:
    """Open a file as text and return a TextIOWrapper.

    Equivalent to the builtin open() in text mode. Provided so callers that
    want an explicit io.TextIOWrapper can use io.text_open() instead of the
    builtin open(), which already works for the common case.
    """
    raw: FileIO = FileIO(file, mode)
    return TextIOWrapper(raw, encoding=encoding)
