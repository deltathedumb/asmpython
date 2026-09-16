"""`tomllib`, as ordinary Python this compiler compiles.

COVERAGE: `load`, `loads`, `TOMLDecodeError` -- exactly `__all__` in the real
module. A full TOML v1.0.0 document: key/value pairs, dotted keys (both in
ordinary key/value lines and inside inline tables), `[table]` and
`[[array of table]]` headers -- dotted, nested, and interleaved with each
other the way the spec's own `fruit` example does -- inline tables, arrays
(multi-line, trailing commas, comments, nested arrays), every string kind
(basic and literal, single- and triple-quoted) with every escape CPython's
basic string accepts -- the letter escapes for backspace, tab, linefeed,
form feed and carriage return, the escaped quote and the escaped backslash
itself, a four-hex unicode escape and an eight-hex one, and the
line-continuation escape inside a multi-line basic string -- integers in
decimal/hex/octal/binary with `_` digit separators, floats with `_`
separators and exponents, and `inf`/`nan`/`+inf`/`-inf`/`+nan`/`-nan`. The
redefinition rules -- a `[table]` cannot reopen a dotted key, an inline table
or an array cannot be mutated afterwards, a key cannot be set twice -- are
ported from the reference algorithm rather than approximated, because those
are exactly the cases a document either is or is not valid TOML and a parser
that is permissive where CPython is strict is a wrong answer wearing a right
name.

NOT COVERED: TOML's date/time literals -- offset date-time, local date-time,
local date, local time. Refused BY NAME with a `TOMLDecodeError`, rather than
approximated: `datetime` is not itself bundled yet (`docs/STDLIB.md`, tier
4), and a hand-rolled stand-in for `datetime.date`/`.time`/`.datetime` that
gets `repr`, `str`, comparison or arithmetic subtly wrong against the real
types would be exactly the "quietly matches the wrong thing" failure this
rebuild exists to avoid -- worse than refusing outright, because it would
look like a supported value until something downstream inspected it. A
document with no date/time value parses and prints identically to CPython's;
one that names a date/time value is refused rather than silently mistyped.

`TOMLDecodeError(msg, doc, pos)` carries the documented `msg`, `doc`, `pos`,
`lineno`, `colno` attributes and is a `ValueError`, matching the real
constructor's ordinary (non-deprecated) form; the legacy free-form-argument
constructor CPython keeps only for backward compatibility is not implemented.

## Why this is a port of the algorithm and not a translation of the regex

The reference parser (`tomllib._parser`) is short because it leans on three
regular expressions for numbers, datetimes and local times. This module has
no dependency on `re` at all -- not because `re` is unavailable (it is
bundled and complete, `docs/STDLIB.md`), but because a hand-scanned character
loop is the more direct statement of "a decimal integer is an optional sign,
then a digit run with `_` allowed only between two digits" than a verbose
regex is, and it is one fewer place a subtle escaping mistake could hide.
Everything else -- the `Flags`/nested-dict bookkeeping that decides whether a
table may be reopened, an inline table mutated, a key set twice -- is kept
close to the reference shape, because that bookkeeping (not the grammar) is
where a differential test actually earns its keep: a table-redefinition rule
that is off by one namespace is not something a happy-path parse would ever
notice.
"""

# Every character this module needs to spell out that a plain source-code
# string literal cannot hold safely is built from its codepoint instead --
# a backslash escape sequence typed into this file's own source is exactly
# the kind of thing an editing tool can silently mangle, and `chr()` cannot
# be misread by anything.
_BS = chr(92)
_TAB = chr(9)
_NL = chr(10)
_CR = chr(13)
_FF = chr(12)
_BKSP = chr(8)
_DQ = chr(34)

_ASCII_CTRL = frozenset(chr(_i) for _i in range(0x20)) | frozenset([chr(0x7F)])
_ILLEGAL_BASIC_STR_CHARS = _ASCII_CTRL - frozenset(_TAB)
_ILLEGAL_MULTILINE_BASIC_STR_CHARS = _ASCII_CTRL - frozenset(_TAB + _NL)
_ILLEGAL_LITERAL_STR_CHARS = _ILLEGAL_BASIC_STR_CHARS
_ILLEGAL_MULTILINE_LITERAL_STR_CHARS = _ILLEGAL_MULTILINE_BASIC_STR_CHARS
_ILLEGAL_COMMENT_CHARS = _ILLEGAL_BASIC_STR_CHARS

_WS = frozenset(" " + _TAB)
_WS_AND_NEWLINE = _WS | frozenset(_NL)
_BARE_KEY_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "-_"
)
_KEY_INITIAL_CHARS = _BARE_KEY_CHARS | frozenset(_DQ + "'")
_DIGITS = frozenset("0123456789")
_HEXDIGITS = frozenset("0123456789abcdefABCDEF")
_OCTDIGITS = frozenset("01234567")
_BINDIGITS = frozenset("01")

_BASIC_STR_ESCAPES = {
    "b": _BKSP,
    "t": _TAB,
    "n": _NL,
    "f": _FF,
    "r": _CR,
    _DQ: _DQ,
    _BS: _BS,
}


class TOMLDecodeError(ValueError):
    """An error raised if a document is not valid TOML.

    Adds the following attributes to ValueError:
    msg: The unformatted error message
    doc: The TOML document being parsed
    pos: The index of doc where parsing failed
    lineno: The line corresponding to pos
    colno: The column corresponding to pos
    """

    def __init__(self, msg, doc, pos):
        self.msg = msg
        self.doc = doc
        self.pos = pos
        lineno = doc.count(_NL, 0, pos) + 1
        if lineno == 1:
            colno = pos + 1
        else:
            # `rfind` rather than the reference implementation's `rindex`:
            # this runtime does not implement `str.rindex` (`re.py`'s own
            # `colno` line hits the same gap and uses `rfind` too). A match
            # is guaranteed here -- `lineno != 1` means a newline precedes
            # `pos` -- so the two calls answer identically for this case;
            # `rindex` differs from `rfind` only in what happens when there
            # is no match at all.
            colno = pos - doc.rfind(_NL, 0, pos)
        self.lineno = lineno
        self.colno = colno
        if pos >= len(doc):
            coord_repr = "end of document"
        else:
            coord_repr = "line %d, column %d" % (lineno, colno)
        super().__init__("%s (at %s)" % (msg, coord_repr))


# ---- scanning primitives ----------------------------------------------------

def _skip_chars(src, pos, chars):
    n = len(src)
    while pos < n and src[pos] in chars:
        pos += 1
    return pos


def _skip_until(src, pos, expect, error_on, error_on_eof):
    idx = src.find(expect, pos)
    if idx == -1:
        new_pos = len(src)
        if error_on_eof:
            raise TOMLDecodeError("Expected %r" % expect, src, new_pos)
    else:
        new_pos = idx
    for ch in src[pos:new_pos]:
        if ch in error_on:
            bad = pos
            while src[bad] not in error_on:
                bad += 1
            raise TOMLDecodeError("Found invalid character %r" % src[bad], src, bad)
    return new_pos


def _skip_comment(src, pos):
    char = src[pos] if pos < len(src) else None
    if char == "#":
        return _skip_until(src, pos + 1, _NL, _ILLEGAL_COMMENT_CHARS, False)
    return pos


def _skip_comments_and_array_ws(src, pos):
    while True:
        before = pos
        pos = _skip_chars(src, pos, _WS_AND_NEWLINE)
        pos = _skip_comment(src, pos)
        if pos == before:
            return pos


# ---- strings ------------------------------------------------------------------

def _parse_hex_char(src, pos, length):
    hex_str = src[pos:pos + length]
    if len(hex_str) != length or not all(c in _HEXDIGITS for c in hex_str):
        raise TOMLDecodeError("Invalid hex value", src, pos)
    pos += length
    code = int(hex_str, 16)
    if not (0 <= code <= 0xD7FF or 0xE000 <= code <= 0x10FFFF):
        raise TOMLDecodeError(
            "Escaped character is not a Unicode scalar value", src, pos)
    return pos, chr(code)


def _parse_basic_str_escape(src, pos, multiline):
    escape_id = src[pos:pos + 2]
    pos += 2
    if multiline and escape_id in (_BS + " ", _BS + _TAB, _BS + _NL):
        if escape_id != _BS + _NL:
            pos = _skip_chars(src, pos, _WS)
            char = src[pos] if pos < len(src) else None
            if char is None:
                return pos, ""
            if char != _NL:
                raise TOMLDecodeError("Unescaped backslash in a string", src, pos)
            pos += 1
        pos = _skip_chars(src, pos, _WS_AND_NEWLINE)
        return pos, ""
    if escape_id == _BS + "u":
        return _parse_hex_char(src, pos, 4)
    if escape_id == _BS + "U":
        return _parse_hex_char(src, pos, 8)
    esc_char = escape_id[1:2]
    if esc_char in _BASIC_STR_ESCAPES:
        return pos, _BASIC_STR_ESCAPES[esc_char]
    raise TOMLDecodeError("Unescaped backslash in a string", src, pos)


def _parse_basic_str(src, pos, multiline):
    error_on = _ILLEGAL_MULTILINE_BASIC_STR_CHARS if multiline else _ILLEGAL_BASIC_STR_CHARS
    result = []
    start = pos
    n = len(src)
    while True:
        if pos >= n:
            raise TOMLDecodeError("Unterminated string", src, pos)
        char = src[pos]
        if char == _DQ:
            if not multiline:
                result.append(src[start:pos])
                return pos + 1, "".join(result)
            if src.startswith(_DQ * 3, pos):
                result.append(src[start:pos])
                return pos + 3, "".join(result)
            pos += 1
            continue
        if char == _BS:
            result.append(src[start:pos])
            pos, piece = _parse_basic_str_escape(src, pos, multiline)
            result.append(piece)
            start = pos
            continue
        if char in error_on:
            raise TOMLDecodeError("Illegal character %r" % char, src, pos)
        pos += 1


def _parse_one_line_basic_str(src, pos):
    return _parse_basic_str(src, pos + 1, multiline=False)


def _parse_literal_str(src, pos):
    pos += 1
    start = pos
    end = _skip_until(src, pos, "'", _ILLEGAL_LITERAL_STR_CHARS, True)
    return end + 1, src[start:end]


def _parse_multiline_str(src, pos, literal):
    pos += 3
    if src.startswith(_NL, pos):
        pos += 1
    if literal:
        delim = "'"
        end = _skip_until(src, pos, "'''", _ILLEGAL_MULTILINE_LITERAL_STR_CHARS, True)
        result = src[pos:end]
        pos = end + 3
    else:
        delim = _DQ
        pos, result = _parse_basic_str(src, pos, multiline=True)
    # Up to two extra closing quotes belong to the string itself -- six quotes
    # in a row closes a triple-quoted string that itself ends with a quote.
    if not src.startswith(delim, pos):
        return pos, result
    pos += 1
    if not src.startswith(delim, pos):
        return pos, result + delim
    pos += 1
    return pos, result + delim * 2


# ---- numbers --------------------------------------------------------------------

def _scan_digit_run(src, pos, allowed):
    """A digit run where `_` may appear only strictly between two digits."""
    n = len(src)
    if pos >= n or src[pos] not in allowed:
        return None
    pos += 1
    while pos < n:
        if src[pos] == "_":
            if pos + 1 < n and src[pos + 1] in allowed:
                pos += 2
                continue
            break
        if src[pos] in allowed:
            pos += 1
            continue
        break
    return pos


def _match_prefixed_int(src, pos):
    """`0x..`/`0o..`/`0b..` -- no sign, lowercase prefix only."""
    kind = src[pos + 1]
    if kind == "x":
        allowed = _HEXDIGITS
    elif kind == "o":
        allowed = _OCTDIGITS
    else:
        allowed = _BINDIGITS
    end = _scan_digit_run(src, pos + 2, allowed)
    if end is None:
        return None
    return end


def _match_decimal_number(src, pos):
    """A decimal int or float: `[+-]?(0|[1-9](_?[0-9])*)` plus an optional
    fractional and/or exponent part. Returns (end, is_float) or None."""
    n = len(src)
    p = pos
    if p < n and src[p] in "+-":
        p += 1
    if p < n and src[p] == "0":
        p += 1
    elif p < n and src[p] in "123456789":
        p = _scan_digit_run(src, p, _DIGITS)
    else:
        return None
    is_float = False
    if p < n and src[p] == "." and p + 1 < n and src[p + 1] in _DIGITS:
        p = _scan_digit_run(src, p + 1, _DIGITS)
        is_float = True
    if p < n and src[p] in "eE":
        look = p + 1
        if look < n and src[look] in "+-":
            look += 1
        if look < n and src[look] in _DIGITS:
            p = _scan_digit_run(src, look, _DIGITS)
            is_float = True
    return p, is_float


def _looks_like_date_or_time(src, pos):
    """A prefix shaped like a TOML date or time literal -- checked so the
    refusal below names the construct rather than reporting a confusing
    'expected newline' a few characters further on."""
    n = len(src)
    if pos + 10 <= n:
        chunk = src[pos:pos + 10]
        if (chunk[0:4].isdigit() and chunk[4] == "-"
                and chunk[5:7].isdigit() and chunk[7] == "-"
                and chunk[8:10].isdigit()):
            return True
    if pos + 8 <= n:
        chunk = src[pos:pos + 8]
        if (chunk[0:2].isdigit() and chunk[2] == ":"
                and chunk[3:5].isdigit() and chunk[5] == ":"
                and chunk[6:8].isdigit()):
            return True
    return False


# ---- keys ------------------------------------------------------------------------

def _parse_key_part(src, pos):
    char = src[pos] if pos < len(src) else None
    if char in _BARE_KEY_CHARS:
        start = pos
        pos = _skip_chars(src, pos, _BARE_KEY_CHARS)
        return pos, src[start:pos]
    if char == "'":
        return _parse_literal_str(src, pos)
    if char == _DQ:
        return _parse_one_line_basic_str(src, pos)
    raise TOMLDecodeError("Invalid initial character for a key part", src, pos)


def _parse_key(src, pos):
    pos, part = _parse_key_part(src, pos)
    key = (part,)
    pos = _skip_chars(src, pos, _WS)
    while True:
        char = src[pos] if pos < len(src) else None
        if char != ".":
            return pos, key
        pos += 1
        pos = _skip_chars(src, pos, _WS)
        pos, part = _parse_key_part(src, pos)
        key = key + (part,)
        pos = _skip_chars(src, pos, _WS)


# ---- the table/array-of-table bookkeeping (ported from the reference parser) ----
# A `[table]` header may not reopen a namespace a dotted key already defined,
# an inline table or array may never be mutated again once closed, and a key
# may not be set twice -- three rules a happy-path parse never exercises, and
# exactly the ones `tests/stdlib/tomllib.py` has to catch a wrong answer in.

class _Flags:
    FROZEN = 0
    EXPLICIT_NEST = 1

    def __init__(self):
        self._flags = {}
        self._pending = set()

    def add_pending(self, key, flag):
        self._pending.add((key, flag))

    def finalize_pending(self):
        for key, flag in self._pending:
            self.set(key, flag, recursive=False)
        self._pending.clear()

    def unset_all(self, key):
        cont = self._flags
        for k in key[:-1]:
            if k not in cont:
                return
            cont = cont[k]["nested"]
        cont.pop(key[-1], None)

    def set(self, key, flag, *, recursive):
        cont = self._flags
        parent, stem = key[:-1], key[-1]
        for k in parent:
            if k not in cont:
                cont[k] = {"flags": set(), "recursive_flags": set(), "nested": {}}
            cont = cont[k]["nested"]
        if stem not in cont:
            cont[stem] = {"flags": set(), "recursive_flags": set(), "nested": {}}
        if recursive:
            cont[stem]["recursive_flags"].add(flag)
        else:
            cont[stem]["flags"].add(flag)

    def is_set(self, key, flag):
        if not key:
            return False
        cont = self._flags
        for k in key[:-1]:
            if k not in cont:
                return False
            inner = cont[k]
            if flag in inner["recursive_flags"]:
                return True
            cont = inner["nested"]
        stem = key[-1]
        if stem in cont:
            c = cont[stem]
            return flag in c["flags"] or flag in c["recursive_flags"]
        return False


def _get_or_create_nest(root, key, access_lists=True):
    cont = root
    for k in key:
        if k not in cont:
            cont[k] = {}
        cont = cont[k]
        if access_lists and isinstance(cont, list):
            cont = cont[-1]
        if not isinstance(cont, dict):
            raise KeyError("There is no nest behind this key")
    return cont


def _append_nest_to_list(root, key):
    cont = _get_or_create_nest(root, key[:-1])
    last_key = key[-1]
    if last_key in cont:
        lst = cont[last_key]
        if not isinstance(lst, list):
            raise KeyError("An object other than list found behind this key")
        lst.append({})
    else:
        cont[last_key] = [{}]


class _Output:
    def __init__(self):
        self.root = {}
        self.flags = _Flags()


# ---- values ------------------------------------------------------------------

def _parse_value(src, pos, parse_float):
    n = len(src)
    char = src[pos] if pos < n else None

    # Order follows the reference parser: unambiguous first characters
    # first, the two literals that need a lookahead next, then numbers.
    if char == _DQ:
        if src.startswith(_DQ * 3, pos):
            return _parse_multiline_str(src, pos, literal=False)
        return _parse_one_line_basic_str(src, pos)
    if char == "'":
        if src.startswith("'''", pos):
            return _parse_multiline_str(src, pos, literal=True)
        return _parse_literal_str(src, pos)
    if char == "t" and src.startswith("true", pos):
        return pos + 4, True
    if char == "f" and src.startswith("false", pos):
        return pos + 5, False
    if char == "[":
        return _parse_array(src, pos, parse_float)
    if char == "{":
        return _parse_inline_table(src, pos, parse_float)

    if _looks_like_date_or_time(src, pos):
        raise TOMLDecodeError(
            "TOML date/time literals are not supported by this parser "
            "(datetime is not yet a bundled module)", src, pos)

    if src.startswith(("0x", "0o", "0b"), pos):
        end = _match_prefixed_int(src, pos)
        if end is not None:
            return end, int(src[pos:end], 0)

    dec = _match_decimal_number(src, pos)
    if dec is not None:
        end, is_float = dec
        text = src[pos:end]
        if is_float:
            return end, parse_float(text)
        return end, int(text, 0)

    first3 = src[pos:pos + 3]
    if first3 in ("inf", "nan"):
        return pos + 3, parse_float(first3)
    first4 = src[pos:pos + 4]
    if first4 in ("+inf", "-inf", "+nan", "-nan"):
        return pos + 4, parse_float(first4)

    raise TOMLDecodeError("Invalid value", src, pos)


def _parse_array(src, pos, parse_float):
    pos += 1
    array = []
    pos = _skip_comments_and_array_ws(src, pos)
    if src.startswith("]", pos):
        return pos + 1, array
    while True:
        pos, val = _parse_value(src, pos, parse_float)
        array.append(val)
        pos = _skip_comments_and_array_ws(src, pos)
        c = src[pos:pos + 1]
        if c == "]":
            return pos + 1, array
        if c != ",":
            raise TOMLDecodeError("Unclosed array", src, pos)
        pos += 1
        pos = _skip_comments_and_array_ws(src, pos)
        if src.startswith("]", pos):
            return pos + 1, array


def _parse_key_value_pair(src, pos, parse_float):
    pos, key = _parse_key(src, pos)
    char = src[pos] if pos < len(src) else None
    if char != "=":
        raise TOMLDecodeError(
            "Expected '=' after a key in a key/value pair", src, pos)
    pos += 1
    pos = _skip_chars(src, pos, _WS)
    pos, value = _parse_value(src, pos, parse_float)
    return pos, key, value


def _parse_inline_table(src, pos, parse_float):
    pos += 1
    root = {}
    flags = _Flags()
    pos = _skip_chars(src, pos, _WS)
    if src.startswith("}", pos):
        return pos + 1, root
    while True:
        pos, key, value = _parse_key_value_pair(src, pos, parse_float)
        key_parent, key_stem = key[:-1], key[-1]
        if flags.is_set(key, _Flags.FROZEN):
            raise TOMLDecodeError(
                "Cannot mutate immutable namespace %r" % (key,), src, pos)
        try:
            nest = _get_or_create_nest(root, key_parent, access_lists=False)
        except KeyError:
            raise TOMLDecodeError("Cannot overwrite a value", src, pos)
        if key_stem in nest:
            raise TOMLDecodeError(
                "Duplicate inline table key %r" % (key_stem,), src, pos)
        nest[key_stem] = value
        pos = _skip_chars(src, pos, _WS)
        c = src[pos:pos + 1]
        if c == "}":
            return pos + 1, root
        if c != ",":
            raise TOMLDecodeError("Unclosed inline table", src, pos)
        if isinstance(value, (dict, list)):
            flags.set(key, _Flags.FROZEN, recursive=True)
        pos += 1
        pos = _skip_chars(src, pos, _WS)


# ---- statements: key/value lines and table headers -----------------------------

def _key_value_rule(src, pos, out, header, parse_float):
    pos, key, value = _parse_key_value_pair(src, pos, parse_float)
    key_parent, key_stem = key[:-1], key[-1]
    abs_key_parent = header + key_parent

    for i in range(1, len(key)):
        cont_key = header + key[:i]
        if out.flags.is_set(cont_key, _Flags.EXPLICIT_NEST):
            raise TOMLDecodeError(
                "Cannot redefine namespace %r" % (cont_key,), src, pos)
        out.flags.add_pending(cont_key, _Flags.EXPLICIT_NEST)

    if out.flags.is_set(abs_key_parent, _Flags.FROZEN):
        raise TOMLDecodeError(
            "Cannot mutate immutable namespace %r" % (abs_key_parent,), src, pos)

    try:
        nest = _get_or_create_nest(out.root, abs_key_parent)
    except KeyError:
        raise TOMLDecodeError("Cannot overwrite a value", src, pos)
    if key_stem in nest:
        raise TOMLDecodeError("Cannot overwrite a value", src, pos)
    if isinstance(value, (dict, list)):
        out.flags.set(header + key, _Flags.FROZEN, recursive=True)
    nest[key_stem] = value
    return pos


def _create_dict_rule(src, pos, out):
    pos += 1
    pos = _skip_chars(src, pos, _WS)
    pos, key = _parse_key(src, pos)
    if out.flags.is_set(key, _Flags.EXPLICIT_NEST) or out.flags.is_set(key, _Flags.FROZEN):
        raise TOMLDecodeError("Cannot declare %r twice" % (key,), src, pos)
    out.flags.set(key, _Flags.EXPLICIT_NEST, recursive=False)
    try:
        _get_or_create_nest(out.root, key)
    except KeyError:
        raise TOMLDecodeError("Cannot overwrite a value", src, pos)
    if not src.startswith("]", pos):
        raise TOMLDecodeError(
            "Expected ']' at the end of a table declaration", src, pos)
    return pos + 1, key


def _create_list_rule(src, pos, out):
    pos += 2
    pos = _skip_chars(src, pos, _WS)
    pos, key = _parse_key(src, pos)
    if out.flags.is_set(key, _Flags.FROZEN):
        raise TOMLDecodeError(
            "Cannot mutate immutable namespace %r" % (key,), src, pos)
    out.flags.unset_all(key)
    out.flags.set(key, _Flags.EXPLICIT_NEST, recursive=False)
    try:
        _append_nest_to_list(out.root, key)
    except KeyError:
        raise TOMLDecodeError("Cannot overwrite a value", src, pos)
    if not src.startswith("]]", pos):
        raise TOMLDecodeError(
            "Expected ']]' at the end of an array declaration", src, pos)
    return pos + 2, key


def _make_safe_parse_float(parse_float):
    """A `parse_float` that returns a dict or list would be indistinguishable
    from a parsed table or array to everything downstream of it."""
    if parse_float is float:
        return float

    def safe_parse_float(text):
        value = parse_float(text)
        if isinstance(value, (dict, list)):
            raise ValueError("parse_float must not return dicts or lists")
        return value

    return safe_parse_float


# ---- the public surface --------------------------------------------------------

def loads(s, /, *, parse_float=float):
    """Parse TOML from a string."""
    src = s.replace(_CR + _NL, _NL)
    n = len(src)
    pos = 0
    out = _Output()
    header = ()
    parse_float = _make_safe_parse_float(parse_float)

    while True:
        pos = _skip_chars(src, pos, _WS)
        if pos >= n:
            break
        char = src[pos]
        if char == _NL:
            pos += 1
            continue
        if char in _KEY_INITIAL_CHARS:
            pos = _key_value_rule(src, pos, out, header, parse_float)
            pos = _skip_chars(src, pos, _WS)
        elif char == "[":
            second_char = src[pos + 1] if pos + 1 < n else None
            out.flags.finalize_pending()
            if second_char == "[":
                pos, header = _create_list_rule(src, pos, out)
            else:
                pos, header = _create_dict_rule(src, pos, out)
            pos = _skip_chars(src, pos, _WS)
        elif char != "#":
            raise TOMLDecodeError("Invalid statement", src, pos)

        pos = _skip_comment(src, pos)
        if pos >= n:
            break
        if src[pos] != _NL:
            raise TOMLDecodeError(
                "Expected newline or end of document after a statement", src, pos)
        pos += 1

    return out.root


def load(fp, /, *, parse_float=float):
    """Parse TOML from a binary file object."""
    data = fp.read()
    try:
        s = data.decode()
    except AttributeError:
        raise TypeError(
            "File must be opened in binary mode, e.g. use `open('foo.toml', 'rb')`")
    return loads(s, parse_float=parse_float)
