"""json module: JSON encoding and decoding.

Supports a practical subset of JSON for asmpython's type system:
  - dumps(obj, indent): serialize int, float, str, bool, None, list, dict
  - loads(s): parse JSON string into int, float, str, list, dict

Limitations vs CPython json:
  - No custom encoder/decoder classes
  - Floats: uses str(x) which may differ from CPython's repr for edge cases
  - Object keys must be strings
  - No ensure_ascii / sort_keys / separators options
"""
from __future__ import annotations


def _dumps_str(s: str) -> str:
    """Escape a Python string to a JSON string literal with surrounding quotes."""
    result = '"'
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            result = result + '\\"'
        elif c == '\\':
            result = result + '\\\\'
        elif c == '\n':
            result = result + '\\n'
        elif c == '\r':
            result = result + '\\r'
        elif c == '\t':
            result = result + '\\t'
        else:
            result = result + c
        i = i + 1
    return result + '"'


def _dumps_val(obj: object, indent: int, depth: int) -> str:
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
        return _dumps_str(obj)
    if isinstance(obj, list):
        if len(obj) == 0:
            return "[]"
        if indent == 0:
            parts: list[str] = []
            for item in obj:
                parts.append(_dumps_val(item, 0, depth + 1))
            return "[" + ", ".join(parts) + "]"
        pad = " " * (indent * (depth + 1))
        close_pad = " " * (indent * depth)
        parts2: list[str] = []
        for item in obj:
            parts2.append(pad + _dumps_val(item, indent, depth + 1))
        return "[\n" + ",\n".join(parts2) + "\n" + close_pad + "]"
    if isinstance(obj, dict):
        if len(obj) == 0:
            return "{}"
        if indent == 0:
            kv: list[str] = []
            for k in obj:
                kv.append(_dumps_str(str(k)) + ": " + _dumps_val(obj[k], 0, depth + 1))
            return "{" + ", ".join(kv) + "}"
        pad3 = " " * (indent * (depth + 1))
        close_pad3 = " " * (indent * depth)
        kv2: list[str] = []
        for k in obj:
            kv2.append(pad3 + _dumps_str(str(k)) + ": " + _dumps_val(obj[k], indent, depth + 1))
        return "{\n" + ",\n".join(kv2) + "\n" + close_pad3 + "}"
    return '"' + str(obj) + '"'


def dumps(obj: object, indent: int = 0) -> str:
    """Serialize obj to a JSON string."""
    return _dumps_val(obj, indent, 0)


def _val_to_json(v: str) -> str:
    """Convert a stringified value to its JSON form (string values need quoting)."""
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


def dumps_dict(obj: dict[str, str], indent: int = 0) -> str:
    """Serialize a dict[str, str] to a JSON string."""
    if len(obj) == 0:
        return "{}"
    kv: list[str] = []
    for k in obj:
        val: str = obj[k]
        kv.append(_dumps_str(k) + ": " + _dumps_str(val))
    return "{" + ", ".join(kv) + "}"


def dumps_dict_int(obj: dict[str, int], indent: int = 0) -> str:
    """Serialize a dict[str, int] to a JSON string."""
    if len(obj) == 0:
        return "{}"
    kv: list[str] = []
    for k in obj:
        val: int = obj[k]
        kv.append(_dumps_str(k) + ": " + str(val))
    return "{" + ", ".join(kv) + "}"


def dumps_list(obj: list[str], indent: int = 0) -> str:
    """Serialize a list[str] to a JSON string."""
    if len(obj) == 0:
        return "[]"
    parts: list[str] = []
    for item in obj:
        parts.append(_dumps_str(item))
    return "[" + ", ".join(parts) + "]"


def dumps_list_int(obj: list[int], indent: int = 0) -> str:
    """Serialize a list[int] to a JSON string."""
    if len(obj) == 0:
        return "[]"
    parts: list[str] = []
    for item in obj:
        parts.append(str(item))
    return "[" + ", ".join(parts) + "]"


# --- loads -------------------------------------------------------------------
# Parser state is threaded via an index variable. To avoid returning mixed
# (value, int) pairs, each _parse_* function writes its result into the
# _parse_result global and returns the new index.

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
    """Parse a JSON string; writes result to _parse_result, returns next index."""
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
    """Deserialize JSON string s. Returns str representation of the parsed value.

    For structured access, use loads_dict() or loads_list() if the root is
    known to be a dict or list, since asmpython can't return object types.
    This function returns the str() of the parsed value for simple cases
    (strings, numbers) and a repr for containers.
    """
    i = _skip_ws(s, 0)
    n = len(s)
    if i >= n:
        raise ValueError("empty JSON input")
    c = s[i]
    if c == '"':
        ni = _parse_string(s, i)
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
        is_float = 0
        if j < n and s[j] == '.':
            is_float = 1
            j = j + 1
            while j < n and s[j] >= '0' and s[j] <= '9':
                j = j + 1
        if j < n and (s[j] == 'e' or s[j] == 'E'):
            is_float = 1
            j = j + 1
            if j < n and (s[j] == '+' or s[j] == '-'):
                j = j + 1
            while j < n and s[j] >= '0' and s[j] <= '9':
                j = j + 1
        return s[i:j]
    return s


def loads_dict(s: str) -> dict:
    """Parse a JSON object string into a dict[str, str] (values stringified)."""
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
        ni = _parse_string(s, i)
        key = _parse_result
        i = _skip_ws(s, ni)
        if i >= n or s[i] != ':':
            raise ValueError("expected ':' at " + str(i))
        i = i + 1
        i = _skip_ws(s, i)
        # parse value as string
        c2 = s[i]
        if c2 == '"':
            ni2 = _parse_string(s, i)
            result[key] = _parse_result
            i = ni2
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
            while j < n and (s[j] >= '0' and s[j] <= '9' or s[j] == '.' or s[j] == 'e' or s[j] == 'E' or s[j] == '+' or s[j] == '-'):
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
    """Parse a JSON array string into a list[str] (values stringified)."""
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
            ni3 = _parse_string(s, i)
            result.append(_parse_result)
            i = ni3
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
            while j < n and (s[j] >= '0' and s[j] <= '9' or s[j] == '.' or s[j] == 'e' or s[j] == 'E'):
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
