"""pickle module: Python object serialization (int/str/float subset).

In asmpython, pickle serializes int, str, and float values to a text format.
The format is not compatible with CPython's binary pickle protocol.
"""
from __future__ import annotations


HIGHEST_PROTOCOL: int = 5
DEFAULT_PROTOCOL: int = 3


def _pack_int(n: int) -> str:
    return "i" + str(n)


def _pack_str(s: str) -> str:
    result: str = 's"'
    i: int = 0
    n: int = len(s)
    while i < n:
        c: str = s[i]
        if c == "\\":
            result = result + "\\\\"
        elif c == '"':
            result = result + '\\"'
        elif c == "\n":
            result = result + "\\n"
        else:
            result = result + c
        i = i + 1
    return result + '"'


def _pack_float(f: float) -> str:
    return "f" + str(f)


def _unpack_int(data: str) -> int:
    if len(data) < 2 or data[0] != "i":
        return 0
    return int(data[1:])


def _unpack_str(data: str) -> str:
    if len(data) < 3 or data[0] != "s" or data[1] != '"':
        return ""
    inner: str = data[2:len(data) - 1]
    result: str = ""
    i: int = 0
    n: int = len(inner)
    while i < n:
        c: str = inner[i]
        if c == "\\" and i + 1 < n:
            nc: str = inner[i + 1]
            if nc == "\\":
                result = result + "\\"
            elif nc == '"':
                result = result + '"'
            elif nc == "n":
                result = result + "\n"
            else:
                result = result + "\\" + nc
            i = i + 2
        else:
            result = result + c
            i = i + 1
    return result


def _unpack_float(data: str) -> float:
    if len(data) < 2 or data[0] != "f":
        return 0.0
    return float(data[1:])


def dumps_int(n: int, protocol: int = -1) -> str:
    """Serialize an int."""
    return _pack_int(n)


def dumps_str(s: str, protocol: int = -1) -> str:
    """Serialize a str."""
    return _pack_str(s)


def dumps_float(f: float, protocol: int = -1) -> str:
    """Serialize a float."""
    return _pack_float(f)


def dumps(n: int, protocol: int = -1, fix_imports: int = 1) -> str:
    """Serialize an int value."""
    return _pack_int(n)


def loads_int(data: str) -> int:
    """Deserialize an int."""
    return _unpack_int(data)


def loads_str(data: str) -> str:
    """Deserialize a str."""
    return _unpack_str(data)


def loads_float(data: str) -> float:
    """Deserialize a float."""
    return _unpack_float(data)


def loads(data: str, fix_imports: int = 1, encoding: str = "ASCII",
          errors: str = "strict") -> int:
    """Deserialize an int value from data."""
    return _unpack_int(data)


class PickleError(Exception):
    """Base pickle exception."""
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg
    def __str__(self) -> str:
        return self.msg


class PicklingError(PickleError):
    """Raised when an object cannot be pickled."""
    pass


class UnpicklingError(PickleError):
    """Raised when there is a problem unpickling an object."""
    pass
