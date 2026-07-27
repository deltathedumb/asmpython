"""json module: JSON encoding and decoding.

Supports a practical subset of JSON for asmpython's type system:
  - dumps(): serialize int, float, str, bool, None, list, and dict
  - loads(): parse JSON strings into the currently supported value forms

Supported dumps options include indent, ensure_ascii, sort_keys, and separators.
Custom encoder/decoder classes and several less-common CPython options remain
outside the current native subset.
"""
from __future__ import annotations


def _hex4(value: int) -> str:
    digits = "0123456789abcdef"
    return (
        digits[(value >> 12) & 15]
        + digits[(value >> 8) & 15]
        + digits[(value >> 4) & 15]
        + digits[value & 15]
    )


def _dumps_str(s: str, ensure_ascii: bool = True) -> str:
    """Escape a Python string to a JSON string literal."""
    result = '"'
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        code = ord(c)
        if c == '"':
            result = result + '\\"'
        elif c == '\\':
            result = result + '\\\\'
        elif c == '\b':
            result = result + '\\b'
        elif c == '\f':
            result = result + '\\f'
        elif c == '\n':
            result = result + '\\n'
        elif c == '\r':
            result = result + '\\r'
        elif c == '\t':
            result = result + '\\t'
        elif code < 32:
            result = result + "\\u" + _hex4(code)
        elif ensure_ascii and code > 127:
            if code <= 65535:
                result = result + "\\u" + _hex4(code)
            else:
                adjusted = code - 65536
                high = 55296 + (adjusted >> 10)
                low = 56320 + (adjusted & 1023)
                result = result + "\\u" + _hex4(high) + "\\u" + _hex4(low)
        else:
            result = result + c
        i = i + 1
    return result + '"'


def _dict_keys(obj: dict, sort_keys: bool) -> list[str]:
    keys: list[str] = []
    for key in obj:
        keys.append(str(key))
    if sort_keys:
        keys.sort()
    return keys


def _dumps_val(
    obj: object,
    indent: int,
    depth: int,
    ensure_ascii: bool,
    sort_keys: bool,
    item_separator: str,
    key_separator: str,
) -> str:
    if obj is None:
        return "null"
    if obj is True:
        return "true"
    if obj is False:
        return "false"
    if isinstance(obj, int):
        return str(obj)
    if isinstance(obj, float):
        return str(obj)
    if isinstance(obj, str):
        return _dumps_str(obj, ensure_ascii)
    if isinstance(obj, list):
        if len(obj) == 0:
            return "[]"
        if indent == 0:
            parts: list[str] = []
            for item in obj:
                parts.append(
                    _dumps_val(
                        item,
                        0,
                        depth + 1,
                        ensure_ascii,
                        sort_keys,
                        item_separator,
                        key_separator,
                    )
                )
            return "[" + item_separator.join(parts) + "]"
        pad = " " * (indent * (depth + 1))
        close_pad = " " * (indent * depth)
        parts2: list[str] = []
        for item in obj:
            parts2.append(
                pad
                + _dumps_val(
                    item,
                    indent,
                    depth + 1,
                    ensure_ascii,
                    sort_keys,
                    item_separator,
                    key_separator,
                )
            )
        return "[\n" + (item_separator + "\n").join(parts2) + "\n" + close_pad + "]"
    if isinstance(obj, dict):
        if len(obj) == 0:
            return "{}"
        keys = _dict_keys(obj, sort_keys)
        if indent == 0:
            kv: list[str] = []
            for key in keys:
                kv.append(
                    _dumps_str(key, ensure_ascii)
                    + key_separator
                    + _dumps_val(
                        obj[key],
                        0,
                        depth + 1,
                        ensure_ascii,
                        sort_keys,
                        item_separator,
                        key_separator,
                    )
                )
            return "{" + item_separator.join(kv) + "}"
        pad3 = " " * (indent * (depth + 1))
        close_pad3 = " " * (indent * depth)
        kv2: list[str] = []
        for key in keys:
            kv2.append(
                pad3
                + _dumps_str(key, ensure_ascii)
                + key_separator
                + _dumps_val(
                    obj[key],
                    indent,
                    depth + 1,
                    ensure_ascii,
                    sort_keys,
                    item_separator,
                    key_separator,
                )
            )
        return "{\n" + (item_separator + "\n").join(kv2) + "\n" + close_pad3 + "}"
    return _dumps_str(str(obj), ensure_ascii)


def dumps(
    obj: object,
    indent: int = 0,
    ensure_ascii: bool = True,
    sort_keys: bool = False,
    separators: tuple[str, str] = (),
) -> str:
    """Serialize obj to a JSON string.

    ``separators`` follows CPython's ``(item_separator, key_separator)`` order.
    An empty tuple selects CPython-compatible defaults for compact or indented
    output.
    """
    item_separator = ", "
    key_separator = ": "
    if indent != 0:
        item_separator = ","
    if len(separators) != 0:
        if len(separators) != 2:
            raise ValueError("separators must contain exactly two strings")
        item_separator = separators[0]
        key_separator = separators[1]
    return _dumps_val(
        obj,
        indent,
        0,
        ensure_ascii,
        sort_keys,
        item_separator,
        key_separator,
    )


def _val_to_json(v: str) -> str:
    """Convert a stringified value to its JSON form."""
    if v == "None":
        return "null"
    if v == "True":
        return "true"
    if v == "False":
        return "false"
    if len(v) == 0:
        return '""'
    c: str = v[0]
    if c == "-" or (c >= "0" and c <= "9"):
        return v
    return _dumps_str(v)


# The `_dumps_val` dispatcher takes its argument as `object` and identifies a
# dict/list at runtime via isinstance -- but an UNBOXED container passed as
# `object` reads back as UNTAGGED, so isinstance(obj, dict/list) is False and
# the container's pointer gets serialized as a bare number. These typed entry
# points already KNOW the shape, so they serialize it directly: they iterate
# the concrete dict/list and hand each ELEMENT (a real str/int value, which
# boxes and identifies correctly) to `_dumps_val`.
def dumps_dict(obj: dict[str, str], indent: int = 0) -> str:
    if len(obj) == 0:
        return "{}"
    kv: list[str] = []
    for key in _dict_keys(obj, False):
        kv.append(_dumps_str(key) + ": " + _dumps_str(obj[key]))
    return "{" + ", ".join(kv) + "}"


def dumps_dict_int(obj: dict[str, int], indent: int = 0) -> str:
    if len(obj) == 0:
        return "{}"
    kv: list[str] = []
    for key in _dict_keys(obj, False):
        kv.append(_dumps_str(key) + ": " + str(obj[key]))
    return "{" + ", ".join(kv) + "}"


def dumps_list(obj: list[str], indent: int = 0) -> str:
    if len(obj) == 0:
        return "[]"
    parts: list[str] = []
    for item in obj:
        parts.append(_dumps_str(item))
    return "[" + ", ".join(parts) + "]"


def dumps_list_int(obj: list[int], indent: int = 0) -> str:
    if len(obj) == 0:
        return "[]"
    parts2: list[str] = []
    for item2 in obj:
        parts2.append(str(item2))
    return "[" + ", ".join(parts2) + "]"


# --- loads -------------------------------------------------------------------

_parse_result: str = ""
_parse_int_result: int = 0
_parse_dict_result: dict = {}
_parse_list_result: list = []


def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and (s[i] == " " or s[i] == "\t" or s[i] == "\n" or s[i] == "\r"):
        i = i + 1
    return i


def _parse_string(s: str, i: int) -> int:
    global _parse_result
    i = i + 1
    result = ""
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            _parse_result = result
            return i + 1
        if c == '\\':
            i = i + 1
            if i >= n:
                break
            esc = s[i]
            if esc == '"':
                result = result + '"'
            elif esc == '\\':
                result = result + '\\'
            elif esc == '/':
                result = result + '/'
            elif esc == 'b':
                result = result + '\b'
            elif esc == 'f':
                result = result + '\f'
            elif esc == 'n':
                result = result + '\n'
            elif esc == 'r':
                result = result + '\r'
            elif esc == 't':
                result = result + '\t'
            else:
                result = result + esc
        else:
            result = result + c
        i = i + 1
    raise ValueError("unterminated string in JSON")


def loads(s: str) -> str:
    """Deserialize a JSON scalar to the current string-form result."""
    i = _skip_ws(s, 0)
    n = len(s)
    if i >= n:
        raise ValueError("empty JSON input")
    c = s[i]
    if c == '"':
        _parse_string(s, i)
        return _parse_result
    if c == 't' and s[i:i + 4] == "true":
        return "True"
    if c == 'f' and s[i:i + 5] == "false":
        return "False"
    if c == 'n' and s[i:i + 4] == "null":
        return "None"
    if c == '-' or (c >= '0' and c <= '9'):
        j = i
        if j < n and s[j] == '-':
            j = j + 1
        while j < n and s[j] >= '0' and s[j] <= '9':
            j = j + 1
        if j < n and s[j] == '.':
            j = j + 1
            while j < n and s[j] >= '0' and s[j] <= '9':
                j = j + 1
        if j < n and (s[j] == 'e' or s[j] == 'E'):
            j = j + 1
            if j < n and (s[j] == '+' or s[j] == '-'):
                j = j + 1
            while j < n and s[j] >= '0' and s[j] <= '9':
                j = j + 1
        return s[i:j]
    return s


def loads_dict(s: str) -> dict[str, str]:
    """Parse a JSON object into a dict[str, str] with stringified values.

    `dict[str, str]`, not a bare `dict`: the values really are strings, and the
    annotation has to say so or a caller's `d[k]` reads a value of unknown kind
    and prints its address.
    """
    result: dict[str, str] = {}
    i = _skip_ws(s, 0)
    n = len(s)
    if i >= n or s[i] != '{':
        raise ValueError("expected JSON object")
    i = i + 1
    i = _skip_ws(s, i)
    if i < n and s[i] == '}':
        return result
    while 1:
        i = _skip_ws(s, i)
        if s[i] != '"':
            raise ValueError("expected key string at " + str(i))
        next_index = _parse_string(s, i)
        key = _parse_result
        i = _skip_ws(s, next_index)
        if i >= n or s[i] != ':':
            raise ValueError("expected ':' at " + str(i))
        i = _skip_ws(s, i + 1)
        c2 = s[i]
        if c2 == '"':
            i = _parse_string(s, i)
            result[key] = _parse_result
        elif c2 == 't' and s[i:i + 4] == "true":
            result[key] = "True"
            i = i + 4
        elif c2 == 'f' and s[i:i + 5] == "false":
            result[key] = "False"
            i = i + 5
        elif c2 == 'n' and s[i:i + 4] == "null":
            result[key] = "None"
            i = i + 4
        else:
            j = i
            if j < n and s[j] == '-':
                j = j + 1
            while j < n and (
                (s[j] >= '0' and s[j] <= '9')
                or s[j] == '.'
                or s[j] == 'e'
                or s[j] == 'E'
                or s[j] == '+'
                or s[j] == '-'
            ):
                j = j + 1
            result[key] = s[i:j]
            i = j
        i = _skip_ws(s, i)
        if i >= n:
            raise ValueError("unterminated object")
        if s[i] == '}':
            return result
        if s[i] != ',':
            raise ValueError("expected ',' in object at " + str(i))
        i = i + 1


def loads_list(s: str) -> list[str]:
    """Parse a JSON array into a list[str] with stringified values."""
    result: list[str] = []
    i = _skip_ws(s, 0)
    n = len(s)
    if i >= n or s[i] != '[':
        raise ValueError("expected JSON array")
    i = i + 1
    i = _skip_ws(s, i)
    if i < n and s[i] == ']':
        return result
    while 1:
        i = _skip_ws(s, i)
        c3 = s[i]
        if c3 == '"':
            i = _parse_string(s, i)
            result.append(_parse_result)
        elif c3 == 't' and s[i:i + 4] == "true":
            result.append("True")
            i = i + 4
        elif c3 == 'f' and s[i:i + 5] == "false":
            result.append("False")
            i = i + 5
        elif c3 == 'n' and s[i:i + 4] == "null":
            result.append("None")
            i = i + 4
        else:
            j = i
            if j < n and s[j] == '-':
                j = j + 1
            while j < n and (
                (s[j] >= '0' and s[j] <= '9')
                or s[j] == '.'
                or s[j] == 'e'
                or s[j] == 'E'
            ):
                j = j + 1
            result.append(s[i:j])
            i = j
        i = _skip_ws(s, i)
        if i >= n:
            raise ValueError("unterminated array")
        if s[i] == ']':
            return result
        if s[i] != ',':
            raise ValueError("expected ',' in array at " + str(i))
        i = i + 1


class JSONDecodeError(Exception):
    def __init__(self, msg: str = "", doc: str = "", pos: int = 0) -> None:
        self.msg: str = msg
        self.doc: str = doc
        self.pos: int = pos

    def __str__(self) -> str:
        return self.msg + " at position " + str(self.pos)


class JSONEncoder:
    def __init__(
        self,
        indent: int = 0,
        sort_keys: bool = False,
        ensure_ascii: bool = True,
        separators: tuple[str, str] = (),
    ) -> None:
        self.indent: int = indent
        self.sort_keys: bool = sort_keys
        self.ensure_ascii: bool = ensure_ascii
        self.separators: tuple[str, str] = separators

    def encode(self, obj: object) -> str:
        return dumps(
            obj,
            self.indent,
            self.ensure_ascii,
            self.sort_keys,
            self.separators,
        )

    def default(self, obj: object) -> str:
        raise TypeError("Object of type " + str(type(obj)) + " is not JSON serializable")


class JSONDecoder:
    def decode(self, s: str) -> str:
        return loads(s)


def dump(
    obj: object,
    fp: str,
    indent: int = 0,
    ensure_ascii: bool = True,
    sort_keys: bool = False,
    separators: tuple[str, str] = (),
) -> None:
    """Serialize obj and write it to a FILE* compatible handle."""
    import os

    text: str = dumps(obj, indent, ensure_ascii, sort_keys, separators)
    os.fputs(text, fp)


def load(fp: str) -> str:
    """Read a JSON document from a FILE* compatible handle."""
    import os

    text: str = ""
    character: int = os.fgetc(fp)
    while character != -1:
        text = text + chr(character)
        character = os.fgetc(fp)
    return loads(text)
