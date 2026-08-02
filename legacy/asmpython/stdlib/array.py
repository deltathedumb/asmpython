"""array module: efficient arrays of numeric values.

Implements a single Array class that covers all typecodes. The backing
store is a list of int or float values. Typecodes follow CPython:

  'b' signed char  (8-bit)   'B' unsigned char
  'h' signed short (16-bit)  'H' unsigned short
  'i' signed int   (32-bit)  'I' unsigned int
  'l' signed long  (32-bit)  'L' unsigned long
  'q' signed long long       'Q' unsigned long long
  'f' float (32-bit)         'd' double (64-bit)
  'u' Py_UNICODE (deprecated) -- stored as int code point

All int typecodes store Python int; 'f'/'d' store Python float.
Overflow is not enforced (values stored as-is).
"""
from __future__ import annotations


_FLOAT_CODES: str = "fd"
_INT_CODES: str = "bBhHiIlLqQu"
_ITEM_SIZES: dict[str, int] = {
    "b": 1, "B": 1,
    "h": 2, "H": 2,
    "i": 4, "I": 4,
    "l": 4, "L": 4,
    "q": 8, "Q": 8,
    "f": 4, "d": 8,
    "u": 2,
}


def _itemsize(typecode: str) -> int:
    sz: str = _ITEM_SIZES.get(typecode, "")
    if len(sz) == 0:
        return 1
    return int(sz)


class array:
    """Efficient array of numeric values of a single type."""

    def __init__(self, typecode: str, initializer: list = []) -> None:
        if len(typecode) != 1:
            raise ValueError("array() argument 1 must be a unicode character, not str")
        self.typecode: str = typecode
        self.itemsize: int = _itemsize(typecode)
        self._data: list = []
        for item in initializer:
            self._data.append(item)

    # --- sequence protocol ---------------------------------------------------

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index: int) -> object:
        n: int = len(self._data)
        if index < 0:
            index = index + n
        if index < 0 or index >= n:
            raise IndexError("array index out of range")
        return self._data[index]

    def __setitem__(self, index: int, value: object) -> None:
        n: int = len(self._data)
        if index < 0:
            index = index + n
        if index < 0 or index >= n:
            raise IndexError("array assignment index out of range")
        self._data[index] = value

    def __eq__(self, other: array) -> int:
        if len(self._data) != len(other._data):
            return 0
        i: int = 0
        while i < len(self._data):
            if self._data[i] != other._data[i]:
                return 0
            i = i + 1
        return 1

    # --- mutation ------------------------------------------------------------

    def append(self, v: object) -> None:
        """Append v to the end of the array."""
        self._data.append(v)

    def extend(self, iterable: list) -> None:
        """Append items from *iterable* to the end of the array."""
        for item in iterable:
            self._data.append(item)

    def insert(self, i: int, v: object) -> None:
        """Insert item *v* before position *i*."""
        n: int = len(self._data)
        if i < 0:
            i = i + n
        if i < 0:
            i = 0
        if i >= n:
            self._data.append(v)
            return
        new_data: list = []
        j: int = 0
        while j < i:
            new_data.append(self._data[j])
            j = j + 1
        new_data.append(v)
        while j < n:
            new_data.append(self._data[j])
            j = j + 1
        self._data = new_data

    def pop(self, i: int = -1) -> object:
        """Remove and return item at index *i* (default last)."""
        n: int = len(self._data)
        if n == 0:
            raise IndexError("pop from empty array")
        if i < 0:
            i = i + n
        if i < 0 or i >= n:
            raise IndexError("pop index out of range")
        item: object = self._data[i]
        new_data: list = []
        j: int = 0
        while j < n:
            if j != i:
                new_data.append(self._data[j])
            j = j + 1
        self._data = new_data
        return item

    def remove(self, v: object) -> None:
        """Remove the first occurrence of *v*."""
        n: int = len(self._data)
        i: int = 0
        while i < n:
            if self._data[i] == v:
                new_data: list = []
                j: int = 0
                while j < n:
                    if j != i:
                        new_data.append(self._data[j])
                    j = j + 1
                self._data = new_data
                return
            i = i + 1
        raise ValueError("array.remove(x): x not in array")

    def reverse(self) -> None:
        """Reverse the items of the array in place."""
        n: int = len(self._data)
        left: int = 0
        right: int = n - 1
        while left < right:
            tmp: object = self._data[left]
            self._data[left] = self._data[right]
            self._data[right] = tmp
            left = left + 1
            right = right - 1

    # --- search / count ------------------------------------------------------

    def index(self, v: object) -> int:
        """Return the smallest index *i* such that self[i] == v."""
        i: int = 0
        n: int = len(self._data)
        while i < n:
            if self._data[i] == v:
                return i
            i = i + 1
        raise ValueError("array.index(x): x not in array")

    def count(self, v: object) -> int:
        """Return the number of occurrences of *v* in the array."""
        c: int = 0
        for item in self._data:
            if item == v:
                c = c + 1
        return c

    # --- conversion ----------------------------------------------------------

    def tolist(self) -> list:
        """Convert the array to a plain list."""
        result: list = []
        for item in self._data:
            result.append(item)
        return result

    def fromlist(self, lst: list) -> None:
        """Append items from list *lst* to the array."""
        for item in lst:
            self._data.append(item)

    def tobytes(self) -> str:
        """Return the array contents as a raw string (bytes representation).

        Each element is packed in little-endian order according to typecode.
        'b'/'B' -> 1 byte, 'h'/'H' -> 2 bytes, 'i'/'I'/'l'/'L'/'f' -> 4,
        'q'/'Q'/'d' -> 8.
        """
        result: str = ""
        sz: int = self.itemsize
        for item in self._data:
            v: int = int(item)
            j: int = 0
            while j < sz:
                result = result + chr(v & 0xFF)
                v = v >> 8
                j = j + 1
        return result

    def frombytes(self, buf: str) -> None:
        """Append items from raw bytes string *buf*."""
        sz: int = self.itemsize
        n: int = len(buf)
        i: int = 0
        while i + sz <= n:
            v: int = 0
            j: int = sz - 1
            while j >= 0:
                v = (v << 8) | ord(buf[i + j])
                j = j - 1
            self._data.append(v)
            i = i + sz

    def tofile(self, fp: int) -> None:
        """Write the array to file object *fp* as raw bytes (stub)."""
        pass

    def fromfile(self, fp: int, n: int) -> None:
        """Read *n* items from file object *fp* (stub)."""
        pass

    def tostring(self) -> str:
        """Deprecated alias for tobytes()."""
        return self.tobytes()

    def fromstring(self, buf: str) -> None:
        """Deprecated alias for frombytes()."""
        self.frombytes(buf)

    def tounicode(self) -> str:
        """Convert a 'u'-typecode array to a string."""
        result: str = ""
        for item in self._data:
            result = result + chr(int(item))
        return result

    def fromunicode(self, s: str) -> None:
        """Extend a 'u'-typecode array with the code points of *s*."""
        i: int = 0
        while i < len(s):
            self._data.append(ord(s[i]))
            i = i + 1

    def buffer_info(self) -> list:
        """Return [address, length] (stub: address is always 0)."""
        return [0, len(self._data)]

    def byteswap(self) -> None:
        """Byteswap all items of the array in place (stub: no-op for pure Python)."""
        pass

    def __repr__(self) -> str:
        return "array(" + self.typecode + ", " + str(self._data) + ")"
