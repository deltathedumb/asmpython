"""Reading TOML.

A recursive-descent parser over the document, one line at a time. TOML is
line-oriented above the value level -- a table header owns every key/value
under it until the next header -- so the outer loop reads lines and only the
value parser needs to look at characters.

WHAT IS COVERED: tables, dotted keys, strings (basic and literal, with the
standard escapes), integers in every base, floats, booleans, arrays and inline
tables. Dates are NOT: they need `datetime`, and answering a string where
CPython answers a date object would be a wrong answer rather than a gap.
"""


class TOMLDecodeError(ValueError):
    """The document is not valid TOML."""


def loads(text):
    """Parse a TOML document from a string into a dict."""
    if not isinstance(text, str):
        raise TypeError("Expected str object, not '" + type(text).__name__
                        + "'")
    root = {}
    here = root
    for raw in text.split("\n"):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("[["):
            if not line.endswith("]]"):
                raise TOMLDecodeError("unterminated array-of-tables header")
            table = _walk(root, _split_key(line[2:-2].strip()), True)
            here = table
            continue
        if line.startswith("["):
            if not line.endswith("]"):
                raise TOMLDecodeError("unterminated table header")
            here = _walk(root, _split_key(line[1:-1].strip()), False)
            continue
        at = _find_eq(line)
        if at < 0:
            raise TOMLDecodeError("expected '=' after a key: " + repr(line))
        parts = _split_key(line[:at].strip())
        target = _walk(here, parts[:-1], False) if len(parts) > 1 else here
        name = parts[-1]
        if name in target:
            raise TOMLDecodeError("cannot redefine " + repr(name))
        target[name] = _value(line[at + 1:].strip())
    return root


def load(fp):
    """Parse from anything with a `read`. Bytes, as the real module requires."""
    data = fp.read()
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return loads(data)


def _strip_comment(line):
    """Drop a trailing `#` comment -- but not one inside a string."""
    out = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(line):
                out.append(line[i + 1])
                i = i + 2
                continue
            if ch == quote:
                quote = ""
        elif ch == '"' or ch == "'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        i = i + 1
    return "".join(out)


def _find_eq(line):
    """The `=` that separates key from value, skipping any inside a string."""
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i = i + 2
                continue
            if ch == quote:
                quote = ""
        elif ch == '"' or ch == "'":
            quote = ch
        elif ch == "=":
            return i
        i = i + 1
    return -1


def _split_key(text):
    """`a.b."c.d"` -- dotted parts, with a quoted part kept whole."""
    parts = []
    current = []
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
            else:
                current.append(ch)
        elif ch == '"' or ch == "'":
            quote = ch
        elif ch == ".":
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i = i + 1
    parts.append("".join(current).strip())
    return [p for p in parts if p != ""]


def _walk(root, parts, array):
    """The table `parts` names, made along the way if it is not there."""
    here = root
    i = 0
    while i < len(parts):
        name = parts[i]
        last = i == len(parts) - 1
        if name not in here:
            here[name] = [] if (array and last) else {}
        if array and last:
            made = {}
            here[name].append(made)
            return made
        here = here[name]
        if isinstance(here, list):
            # An array of tables: the current one is the LAST appended.
            here = here[-1]
        i = i + 1
    return here


def _value(text):
    text = text.strip()
    if not text:
        raise TOMLDecodeError("expected a value")
    first = text[0]
    if first == '"' or first == "'":
        return _string(text)
    if first == "[":
        return _array(text)
    if first == "{":
        return _inline(text)
    if text == "true":
        return True
    if text == "false":
        return False
    return _number(text)


def _string(text):
    quote = text[0]
    if len(text) < 2 or text[-1] != quote:
        raise TOMLDecodeError("unterminated string")
    body = text[1:-1]
    if quote == "'":
        # A LITERAL STRING TAKES NO ESCAPES; the backslash is a backslash.
        return body
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == "r":
                out.append("\r")
            elif nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            elif nxt == "b":
                out.append("\b")
            elif nxt == "f":
                out.append("\f")
            elif nxt == "u" and i + 5 < len(body) + 1:
                out.append(chr(int(body[i + 2:i + 6], 16)))
                i = i + 6
                continue
            else:
                raise TOMLDecodeError("unknown escape: \\" + nxt)
            i = i + 2
            continue
        out.append(ch)
        i = i + 1
    return "".join(out)


def _split_items(body):
    """The top-level commas of an array or inline table."""
    items = []
    depth = 0
    quote = ""
    current = []
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            current.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(body):
                current.append(body[i + 1])
                i = i + 2
                continue
            if ch == quote:
                quote = ""
        elif ch == '"' or ch == "'":
            quote = ch
            current.append(ch)
        elif ch == "[" or ch == "{":
            depth = depth + 1
            current.append(ch)
        elif ch == "]" or ch == "}":
            depth = depth - 1
            current.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i = i + 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _array(text):
    if not text.endswith("]"):
        raise TOMLDecodeError("unterminated array")
    return [_value(item) for item in _split_items(text[1:-1])]


def _inline(text):
    if not text.endswith("}"):
        raise TOMLDecodeError("unterminated inline table")
    out = {}
    for item in _split_items(text[1:-1]):
        at = _find_eq(item)
        if at < 0:
            raise TOMLDecodeError("expected '=' in an inline table")
        out[_split_key(item[:at].strip())[-1]] = _value(item[at + 1:].strip())
    return out


def _number(text):
    """An integer or a float. UNDERSCORES ARE SEPARATORS and are dropped."""
    body = text.replace("_", "")
    if body.startswith("0x") or body.startswith("0X"):
        return int(body[2:], 16)
    if body.startswith("0o") or body.startswith("0O"):
        return int(body[2:], 8)
    if body.startswith("0b") or body.startswith("0B"):
        return int(body[2:], 2)
    if body in ("inf", "+inf"):
        return float("inf")
    if body == "-inf":
        return float("-inf")
    if body in ("nan", "+nan", "-nan"):
        return float("nan")
    if "." in body or "e" in body or "E" in body:
        return float(body)
    try:
        return int(body)
    except ValueError:
        raise TOMLDecodeError("invalid value: " + repr(text))
