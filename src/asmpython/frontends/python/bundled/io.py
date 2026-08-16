"""In-memory streams.

`StringIO` and `BytesIO` are the whole of `io` that needs no operating system:
a buffer, a position, and the file protocol over them. Everything else in the
module -- `open`, the buffered wrappers, the encodings layer -- is about real
files, which a freestanding image may not have at all.

THE PIECES ARE KEPT AND JOINED ONCE. Appending to a string per `write` is
quadratic in the number of writes, which for a stream that exists to be
written to is the wrong shape; the parts are joined when the value is read.
"""


class StringIO:
    """A text stream over a string in memory."""

    def __init__(self, initial_value=""):
        self._parts = [initial_value] if initial_value else []
        self._pos = len(initial_value)
        self._closed = False

    def write(self, text):
        if self._closed:
            raise ValueError("I/O operation on closed file")
        if not isinstance(text, str):
            raise TypeError("string argument expected, got '"
                            + type(text).__name__ + "'")
        self._parts.append(text)
        self._pos = self._pos + len(text)
        return len(text)

    def writelines(self, lines):
        for one in lines:
            self.write(one)

    def getvalue(self):
        if len(self._parts) > 1:
            self._parts = ["".join(self._parts)]
        return self._parts[0] if self._parts else ""

    def read(self, size=-1):
        held = self.getvalue()
        if size is None or size < 0:
            out = held[self._pos:]
            self._pos = len(held)
            return out
        out = held[self._pos:self._pos + size]
        self._pos = self._pos + len(out)
        return out

    def readline(self, size=-1):
        held = self.getvalue()
        at = held.find("\n", self._pos)
        end = len(held) if at < 0 else at + 1
        out = held[self._pos:end]
        self._pos = end
        return out

    def readlines(self, hint=-1):
        out = []
        for _ in range(1000000):
            line = self.readline()
            if not line:
                break
            out.append(line)
        return out

    def seek(self, pos, whence=0):
        held = self.getvalue()
        if whence == 1:
            pos = self._pos + pos
        elif whence == 2:
            pos = len(held) + pos
        self._pos = pos
        return pos

    def tell(self):
        return self._pos

    def truncate(self, size=None):
        held = self.getvalue()
        at = self._pos if size is None else size
        self._parts = [held[:at]]
        return at

    def flush(self):
        return None

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def readable(self):
        return True

    def writable(self):
        return True

    def seekable(self):
        return True

    def __iter__(self):
        return iter(self.readlines())

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


class BytesIO:
    """The same, over bytes."""

    def __init__(self, initial_bytes=b""):
        self._parts = [initial_bytes] if initial_bytes else []
        self._pos = len(initial_bytes)
        self._closed = False

    def write(self, data):
        if self._closed:
            raise ValueError("I/O operation on closed file")
        self._parts.append(bytes(data))
        self._pos = self._pos + len(data)
        return len(data)

    def getvalue(self):
        if len(self._parts) > 1:
            self._parts = [b"".join(self._parts)]
        return self._parts[0] if self._parts else b""

    def read(self, size=-1):
        held = self.getvalue()
        if size is None or size < 0:
            out = held[self._pos:]
            self._pos = len(held)
            return out
        out = held[self._pos:self._pos + size]
        self._pos = self._pos + len(out)
        return out

    def seek(self, pos, whence=0):
        held = self.getvalue()
        if whence == 1:
            pos = self._pos + pos
        elif whence == 2:
            pos = len(held) + pos
        self._pos = pos
        return pos

    def tell(self):
        return self._pos

    def flush(self):
        return None

    def close(self):
        self._closed = True

    @property
    def closed(self):
        return self._closed

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        self.close()
        return False


class UnsupportedOperation(OSError, ValueError):
    """What a stream raises for something its kind cannot do."""
