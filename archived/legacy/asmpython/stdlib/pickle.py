"""pickle module: Python object serialization.

Implements a text-based pickle protocol for the types asmpython supports:
int, str, float, list (of int/str/float), and dict (str keys, str/int/float values).

The wire format is intentionally NOT compatible with CPython's binary pickle
protocol; it is a compact human-readable format designed for round-tripping
within asmpython programs.

Format tokens:
  iNNN             -- integer NNN
  f1.23            -- float 1.23
  s"..."           -- string (double-quoted, backslash-escaped)
  n                -- None
  t                -- True
  F                -- False
  [i1,i2,...]      -- list
  {s"k":i1,...}    -- dict
"""
from __future__ import annotations


HIGHEST_PROTOCOL: int = 5
DEFAULT_PROTOCOL: int = 3


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


# ---------------------------------------------------------------------------
# Internal serialisers
# ---------------------------------------------------------------------------

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
        elif c == "\r":
            result = result + "\\r"
        elif c == "\t":
            result = result + "\\t"
        else:
            result = result + c
        i = i + 1
    return result + '"'


def _pack_any(obj: object) -> str:
    if obj is None:
        return "n"
    if obj is True:
        return "t"
    if obj is False:
        return "F"
    if isinstance(obj, float):
        return "f" + str(obj)
    if isinstance(obj, int):
        return "i" + str(obj)
    if isinstance(obj, str):
        return _pack_str(obj)
    if isinstance(obj, list):
        return _pack_list(obj)
    if isinstance(obj, dict):
        return _pack_dict(obj)
    raise PicklingError("cannot pickle " + str(type(obj)))


def _pack_list(lst: list) -> str:
    parts: list[str] = []
    for item in lst:
        parts.append(_pack_any(item))
    result: str = "["
    i: int = 0
    while i < len(parts):
        if i > 0:
            result = result + ","
        result = result + parts[i]
        i = i + 1
    return result + "]"


def _pack_dict(d: dict) -> str:
    result: str = "{"
    first: int = 1
    for k in d:
        if not first:
            result = result + ","
        first = 0
        result = result + _pack_str(k) + ":" + _pack_any(d[k])
    return result + "}"


# ---------------------------------------------------------------------------
# Internal deserialisers
# ---------------------------------------------------------------------------

# Parser state via global index (mirrors json.py's approach to avoid
# returning (value, int) mixed pairs from typed sub-parsers).
_p_result: str = ""
_p_int_result: int = 0
_p_float_result: float = 0.0


def _p_parse_str(s: str, i: int) -> int:
    """Parse s"..." starting at i (i points at 's'). Returns next index."""
    global _p_result
    i = i + 2  # skip s"
    result: str = ""
    n: int = len(s)
    while i < n:
        c: str = s[i]
        if c == '"':
            _p_result = result
            return i + 1
        if c == "\\" and i + 1 < n:
            nc: str = s[i + 1]
            if nc == "\\":
                result = result + "\\"
            elif nc == '"':
                result = result + '"'
            elif nc == "n":
                result = result + "\n"
            elif nc == "r":
                result = result + "\r"
            elif nc == "t":
                result = result + "\t"
            else:
                result = result + "\\" + nc
            i = i + 2
        else:
            result = result + c
            i = i + 1
    raise UnpicklingError("unterminated string in pickle data")


def _p_parse_num(s: str, i: int) -> int:
    """Parse a number token (iNNN or fNNN). Returns next index; stores in globals."""
    global _p_result, _p_int_result, _p_float_result
    kind: str = s[i]
    i = i + 1
    start: int = i
    n: int = len(s)
    while i < n:
        c: str = s[i]
        if c == "," or c == "]" or c == "}" or c == " ":
            break
        i = i + 1
    token: str = s[start:i]
    if kind == "i":
        _p_int_result = int(token)
        _p_result = token
    else:
        _p_float_result = float(token)
        _p_result = token
    return i


def _p_parse_value(s: str, i: int) -> int:
    """Parse one value from s starting at i. Returns next index.

    After calling, inspect the leading character of s[orig_i] to know which
    global holds the result (or read _p_result for string form).
    """
    global _p_result
    n: int = len(s)
    if i >= n:
        raise UnpicklingError("unexpected end of pickle data")
    c: str = s[i]
    if c == "n":
        _p_result = "None"
        return i + 1
    if c == "t":
        _p_result = "True"
        return i + 1
    if c == "F":
        _p_result = "False"
        return i + 1
    if c == "s":
        return _p_parse_str(s, i)
    if c == "i" or c == "f":
        return _p_parse_num(s, i)
    if c == "[":
        _p_result = "__list__"
        return i  # caller must call _p_parse_list
    if c == "{":
        _p_result = "__dict__"
        return i  # caller must call _p_parse_dict
    raise UnpicklingError("unknown pickle token '" + c + "' at " + str(i))


def _p_parse_list(s: str, i: int) -> list:
    """Parse a [...] list starting at i (i points at '[')."""
    result: list = []
    i = i + 1  # skip [
    n: int = len(s)
    if i < n and s[i] == "]":
        return result
    while i < n:
        # Skip comma
        if s[i] == ",":
            i = i + 1
        if i >= n:
            break
        c: str = s[i]
        if c == "]":
            break
        if c == "[":
            sub: list = _p_parse_list(s, i)
            result.append(sub)
            # advance past the sublist — find matching ]
            depth: int = 1
            i = i + 1
            while i < n and depth > 0:
                if s[i] == "[":
                    depth = depth + 1
                elif s[i] == "]":
                    depth = depth - 1
                i = i + 1
        elif c == "{":
            sub_dict: dict = _p_parse_dict(s, i)
            result.append(sub_dict)
            depth2: int = 1
            i = i + 1
            while i < n and depth2 > 0:
                if s[i] == "{":
                    depth2 = depth2 + 1
                elif s[i] == "}":
                    depth2 = depth2 - 1
                i = i + 1
        elif c == "s":
            ni: int = _p_parse_str(s, i)
            result.append(_p_result)
            i = ni
        elif c == "n":
            result.append(0)  # None -> 0
            i = i + 1
        elif c == "t":
            result.append(1)  # True -> 1
            i = i + 1
        elif c == "F":
            result.append(0)  # False -> 0
            i = i + 1
        elif c == "i":
            ni2: int = _p_parse_num(s, i)
            result.append(_p_int_result)
            i = ni2
        elif c == "f":
            ni3: int = _p_parse_num(s, i)
            result.append(_p_float_result)
            i = ni3
        else:
            raise UnpicklingError("unexpected char '" + c + "' in list at " + str(i))
    return result


def _p_parse_dict(s: str, i: int) -> dict:
    """Parse a {...} dict starting at i (i points at '{')."""
    result: dict = {}
    i = i + 1  # skip {
    n: int = len(s)
    if i < n and s[i] == "}":
        return result
    while i < n:
        if s[i] == ",":
            i = i + 1
        if i >= n:
            break
        if s[i] == "}":
            break
        # Parse key (must be a string).
        if s[i] != "s":
            raise UnpicklingError("dict key must be a string at " + str(i))
        ni_k: int = _p_parse_str(s, i)
        key: str = _p_result
        i = ni_k
        if i >= n or s[i] != ":":
            raise UnpicklingError("expected ':' at " + str(i))
        i = i + 1
        # Parse value.
        c: str = s[i]
        if c == "s":
            ni_v: int = _p_parse_str(s, i)
            result[key] = _p_result
            i = ni_v
        elif c == "i":
            ni_v2: int = _p_parse_num(s, i)
            result[key] = _p_int_result
            i = ni_v2
        elif c == "f":
            ni_v3: int = _p_parse_num(s, i)
            result[key] = _p_float_result
            i = ni_v3
        elif c == "n":
            result[key] = 0
            i = i + 1
        elif c == "t":
            result[key] = 1
            i = i + 1
        elif c == "F":
            result[key] = 0
            i = i + 1
        else:
            raise UnpicklingError("unsupported value type '" + c + "' in dict at " + str(i))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def dumps(obj: object, protocol: int = -1, fix_imports: int = 1) -> str:
    """Serialize *obj* to a pickle string."""
    return _pack_any(obj)


def dumps_int(n: int, protocol: int = -1) -> str:
    """Serialize an int."""
    return "i" + str(n)


def dumps_str(s: str, protocol: int = -1) -> str:
    """Serialize a str."""
    return _pack_str(s)


def dumps_float(f: float, protocol: int = -1) -> str:
    """Serialize a float."""
    return "f" + str(f)


def dumps_list(lst: list, protocol: int = -1) -> str:
    """Serialize a list."""
    return _pack_list(lst)


def dumps_dict(d: dict, protocol: int = -1) -> str:
    """Serialize a dict (string keys)."""
    return _pack_dict(d)


def loads(data: str, fix_imports: int = 1, encoding: str = "ASCII",
          errors: str = "strict") -> object:
    """Deserialize *data* from a pickle string.

    Returns a typed value:
    - "i" prefix  -> int
    - "f" prefix  -> float
    - "s" prefix  -> str
    - "["         -> list
    - "{"         -> dict
    - "n"         -> 0 (None)
    - "t" / "F"   -> 1 / 0 (True/False)
    """
    if len(data) == 0:
        raise UnpicklingError("empty pickle data")
    c: str = data[0]
    if c == "i":
        return int(data[1:])
    if c == "f":
        return float(data[1:])
    if c == "s":
        _p_parse_str(data, 0)
        return _p_result
    if c == "[":
        return _p_parse_list(data, 0)
    if c == "{":
        return _p_parse_dict(data, 0)
    if c == "n":
        return 0
    if c == "t":
        return 1
    if c == "F":
        return 0
    raise UnpicklingError("unknown pickle token '" + c + "'")


def loads_int(data: str) -> int:
    """Deserialize an int."""
    if len(data) < 2 or data[0] != "i":
        return 0
    return int(data[1:])


def loads_str(data: str) -> str:
    """Deserialize a str."""
    _p_parse_str(data, 0)
    return _p_result


def loads_float(data: str) -> float:
    """Deserialize a float."""
    if len(data) < 2 or data[0] != "f":
        return 0.0
    return float(data[1:])


def loads_list(data: str) -> list:
    """Deserialize a list."""
    return _p_parse_list(data, 0)


def loads_dict(data: str) -> dict:
    """Deserialize a dict."""
    return _p_parse_dict(data, 0)


# ---------------------------------------------------------------------------
# Pickler / Unpickler classes (stream-oriented API)
# ---------------------------------------------------------------------------

class Pickler:
    """Write pickled data to a file-like object (string accumulator)."""

    def __init__(self, protocol: int = -1) -> None:
        self.protocol: int = protocol if protocol >= 0 else DEFAULT_PROTOCOL
        self._buf: str = ""

    def dump(self, obj: object) -> None:
        """Pickle *obj* and append to internal buffer."""
        self._buf = self._buf + dumps(obj)

    def getbuf(self) -> str:
        """Return the accumulated pickle data."""
        return self._buf

    def clear_memo(self) -> None:
        """Clear the memo (no-op; no object deduplication in this implementation)."""
        pass


class Unpickler:
    """Read and reconstruct a pickled object from a string."""

    def __init__(self, data: str = "") -> None:
        self._data: str = data

    def load(self) -> object:
        """Unpickle and return the next object from the data."""
        return loads(self._data)
