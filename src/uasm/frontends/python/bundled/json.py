"""`json`, as ordinary Python this compiler compiles.

COVERAGE: `dumps`, `loads`, `dump`, `load`, `JSONEncoder`, `JSONDecoder`,
`JSONDecodeError` with real `.msg`/`.doc`/`.pos`/`.lineno`/`.colno`. Every
keyword `dumps`/`JSONEncoder` document: `skipkeys`, `ensure_ascii`,
`check_circular`, `allow_nan`, `indent` (an int or a string), `separators`,
`default`, `sort_keys`. Every keyword `loads`/`JSONDecoder` document:
`parse_float`, `parse_int`, `parse_constant`, `object_hook`,
`object_pairs_hook`, `strict`. `dump`/`load` take any object with
`.write`/`.read` -- not necessarily a real file.

NOT COVERED: the `cls=` parameter of the four top-level functions -- neither
is in the signature this module states, so passing it is an ordinary
`TypeError: unexpected keyword argument` rather than a special refusal.
Subclassing `JSONEncoder`/`JSONDecoder` to change ONE method still works,
because that is what the classes below actually are. `loads` decodes `bytes`
and `bytearray` as UTF-8 rather than sniffing a BOM for UTF-16/32 the way
CPython's `detect_encoding` does -- everything this rebuild produces and
everything a real JSON API sends is UTF-8, and sniffing four more encodings
to reject three of them is a lot of code for a case that does not arise here.
`repr()`/`.decode` differ in nothing else: a lone or unpaired surrogate is a
CPython question this module does not have to answer differently.

## Why a hand-written scanner and not `eval`

`eval("{...}")` accepts a DIFFERENT grammar: Python tuples where JSON has no
such thing, single-quoted strings JSON forbids, `True`/`False`/`None` where a
JSON document spells `true`/`false`/`null`, and no `\\uXXXX` bare escape or
`NaN`/`Infinity` at all -- and it silently ACCEPTS Python syntax JSON must
REJECT, which is worse than refusing. So this is a real recursive-descent
parser, one character at a time, and it exists because the grammars are not
the same language wearing two different spellings.

## Why hand-written rather than `re`

CPython's own pure-Python fallback (`json/decoder.py`) uses three compiled
patterns for whitespace, numbers and string chunks. This module is a
character-by-character port of exactly that algorithm -- the same positions,
the same three-tier fallback a `{`, `[` or bare value takes, the same
"illegal trailing comma" and "expecting ':' delimiter" messages at the same
offsets -- with each pattern's job done by a small function instead. What
matters is being byte-identical to CPython's ERROR POSITIONS, which are
observable (`JSONDecodeError.pos`/`.lineno`/`.colno`), and porting the
algorithm the positions came from is the direct way to keep them exact rather
than a way around `re`.

## The number grammar, kept exactly as narrow as the regex it replaces

`-?(?:0|[1-9][0-9]*)(\\.[0-9]+)?([eE][-+]?[0-9]+)?` matches `0`, `-0`, and any
run of digits with no leading zero -- and it matches `01` as JUST the leading
`0`, leaving `1` for whatever reads the character after the number to choke
on. Copying the SHAPE of that grammar (mandatory integer part, each of the
fraction and exponent groups all-or-nothing) is what keeps `01`, `1.`, `1e`
and `-` failing exactly where CPython fails them rather than being accepted
as numbers or rejected one character too early or late.
"""

# ── shared: whitespace and the number grammar ───────────────────────────────
# JSON's OWN four, not `str.isspace()`'s wider set: form feed and vertical tab
# are not JSON whitespace and are not skipped here.
_WS = " \t\n\r"

_DIGITS = "0123456789"
_DIGITS19 = "123456789"
_HEXDIGITS = "0123456789abcdefABCDEF"


def _skip_ws(s, n, pos):
    while pos < n and s[pos] in _WS:
        pos = pos + 1
    return pos


def _match_number(s, n, idx):
    """`-?(?:0|[1-9][0-9]*)(\\.[0-9]+)?([eE][-+]?[0-9]+)?`, matched by hand.

    Answers `(end, is_float)` or `None` for "not a number here at all" -- the
    caller still has `NaN`/`Infinity`/`-Infinity` to try. THE FRACTION AND
    EXPONENT ARE ALL-OR-NOTHING: probing ahead with `j` and only committing
    `i = j` once a full group is seen is what leaves `1.` matching just `1`
    rather than raising or consuming the lone dot.
    """
    i = idx
    if i < n and s[i] == "-":
        i = i + 1
    if i < n and s[i] == "0":
        i = i + 1
    elif i < n and s[i] in _DIGITS19:
        i = i + 1
        while i < n and s[i] in _DIGITS:
            i = i + 1
    else:
        return None
    is_float = False
    if i < n and s[i] == ".":
        j = i + 1
        if j < n and s[j] in _DIGITS:
            j = j + 1
            while j < n and s[j] in _DIGITS:
                j = j + 1
            i = j
            is_float = True
    if i < n and (s[i] == "e" or s[i] == "E"):
        j = i + 1
        if j < n and (s[j] == "+" or s[j] == "-"):
            j = j + 1
        if j < n and s[j] in _DIGITS:
            j = j + 1
            while j < n and s[j] in _DIGITS:
                j = j + 1
            i = j
            is_float = True
    return i, is_float


# ── the error ────────────────────────────────────────────────────────────────

class JSONDecodeError(ValueError):
    """A malformed document, and exactly where in it parsing gave up.

    `pos` is an offset into `doc`; `lineno`/`colno` are derived from it the
    same way CPython derives them, so a caller printing `f"{e.lineno}:
    {e.colno}"` sees what CPython's caller would see for the same input.
    """

    def __init__(self, msg, doc, pos):
        lineno = doc.count("\n", 0, pos) + 1
        colno = pos - doc.rfind("\n", 0, pos)
        text = "%s: line %d column %d (char %d)" % (msg, lineno, colno, pos)
        super().__init__(text)
        self.msg = msg
        self.doc = doc
        self.pos = pos
        self.lineno = lineno
        self.colno = colno


# ── the decoder ──────────────────────────────────────────────────────────────

_BACKSLASH = {
    '"': '"', "\\": "\\", "/": "/",
    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}

_CONSTANTS = {
    "-Infinity": float("-inf"),
    "Infinity": float("inf"),
    "NaN": float("nan"),
}


def _parse_constant(name):
    return _CONSTANTS[name]


def _decode_uXXXX(s, n, pos):
    """The four hex digits right after the `u` at `pos`. Raises naming `pos`
    -- the `u` itself -- rather than where the bad digit is, because that is
    where CPython's own `\\uXXXX` error points."""
    start = pos + 1
    hexpart = s[start:start + 4]
    if len(hexpart) == 4:
        ok = True
        for ch in hexpart:
            if ch not in _HEXDIGITS:
                ok = False
                break
        if ok:
            return int(hexpart, 16)
    raise JSONDecodeError("Invalid \\uXXXX escape", s, pos)


def _scan_string(s, n, end, strict):
    """A JSON string, `end` past the opening quote. `strict` (CPython's own
    name) is whether a literal 0x00-0x1F control character inside the quotes
    is an error rather than passed through -- true is the JSON rule."""
    begin = end - 1
    chunks = []
    while True:
        start = end
        while end < n:
            ch = s[end]
            if ch == '"' or ch == "\\" or ord(ch) < 0x20:
                break
            end = end + 1
        if end >= n:
            raise JSONDecodeError("Unterminated string starting at", s, begin)
        if start < end:
            chunks.append(s[start:end])
        terminator = s[end]
        end = end + 1
        if terminator == '"':
            break
        if terminator != "\\":
            if strict:
                # THE POSITION IS THE CONTROL CHARACTER ITSELF (`end - 1`),
                # not the char after it, and the MESSAGE NAMES NEITHER THE
                # CHARACTER NOR A COLON -- both differ from the pure-Python
                # fallback `json.decoder` ships as source, because the
                # ORACLE runs its C accelerator (`_json.scanstring`) and
                # that is a second, independently-written implementation
                # with its own wording. Checked against the running
                # interpreter rather than against the module's own source.
                raise JSONDecodeError(
                    "Invalid control character at", s, end - 1)
            chunks.append(terminator)
            continue
        if end >= n:
            raise JSONDecodeError("Unterminated string starting at", s, begin)
        esc = s[end]
        if esc != "u":
            char = _BACKSLASH.get(esc)
            if char is None:
                # THE BACKSLASH'S OWN POSITION (`end - 1`), for the same
                # reason: the C accelerator points at the escape rather
                # than the character after it, and drops the character
                # from the message entirely.
                raise JSONDecodeError("Invalid \\escape", s, end - 1)
            end = end + 1
        else:
            uni = _decode_uXXXX(s, n, end)
            end = end + 5
            # A SURROGATE PAIR IS TWO ESCAPES, and only the first is required
            # to be a high surrogate: an astral character JSON cannot spell
            # any other way. A high surrogate NOT followed by a low one is
            # kept as the lone surrogate `chr()` makes of it, same as CPython.
            if (0xd800 <= uni <= 0xdbff and end + 1 < n
                    and s[end] == "\\" and s[end + 1] == "u"):
                uni2 = _decode_uXXXX(s, n, end + 1)
                if 0xdc00 <= uni2 <= 0xdfff:
                    uni = 0x10000 + (((uni - 0xd800) << 10) | (uni2 - 0xdc00))
                    end = end + 6
            char = chr(uni)
        chunks.append(char)
    return "".join(chunks), end


class JSONDecoder:
    """Parses a document one call to `_scan_once` at a time.

    THE POSITION ARITHMETIC IS THE MODULE'S CONTRACT. Every `raise` below
    names the exact CPython message and the exact offset CPython's own
    `json/decoder.py` would report for the same malformed input -- checked
    against it rather than assumed, because a decoder that raises
    `JSONDecodeError` with the wrong `.pos` is as wrong as one that raises
    the wrong exception.
    """

    def __init__(self, *, object_hook=None, parse_float=None, parse_int=None,
                 parse_constant=None, strict=True, object_pairs_hook=None):
        self.object_hook = object_hook
        self.object_pairs_hook = object_pairs_hook
        self.parse_float = parse_float if parse_float is not None else float
        self.parse_int = parse_int if parse_int is not None else int
        self.parse_constant = (parse_constant if parse_constant is not None
                                else _parse_constant)
        self.strict = strict

    def decode(self, s):
        n = len(s)
        idx = _skip_ws(s, n, 0)
        obj, end = self._scan_once(s, n, idx)
        end = _skip_ws(s, n, end)
        if end != n:
            raise JSONDecodeError("Extra data", s, end)
        return obj

    def raw_decode(self, s, idx=0):
        """`decode`, but trailing data past the document is not an error --
        the second half of the answer is where it began."""
        return self._scan_once(s, len(s), idx)

    def _scan_once(self, s, n, idx):
        ch = s[idx:idx + 1]
        if ch == '"':
            return _scan_string(s, n, idx + 1, self.strict)
        if ch == "{":
            return self._scan_object(s, n, idx + 1)
        if ch == "[":
            return self._scan_array(s, n, idx + 1)
        if ch == "n" and s[idx:idx + 4] == "null":
            return None, idx + 4
        if ch == "t" and s[idx:idx + 4] == "true":
            return True, idx + 4
        if ch == "f" and s[idx:idx + 5] == "false":
            return False, idx + 5
        got = _match_number(s, n, idx)
        if got is not None:
            end, is_float = got
            text = s[idx:end]
            if is_float:
                return self.parse_float(text), end
            return self.parse_int(text), end
        # NaN/Infinity/-Infinity ARE OUTSIDE THE JSON SPEC and CPython
        # accepts them anyway; refusing them here would be a gratuitous
        # divergence from the oracle rather than a stricter reading of it.
        if ch == "N" and s[idx:idx + 3] == "NaN":
            return self.parse_constant("NaN"), idx + 3
        if ch == "I" and s[idx:idx + 8] == "Infinity":
            return self.parse_constant("Infinity"), idx + 8
        if ch == "-" and s[idx:idx + 9] == "-Infinity":
            return self.parse_constant("-Infinity"), idx + 9
        raise JSONDecodeError("Expecting value", s, idx)

    def _scan_object(self, s, n, end):
        pairs = []
        nextchar = s[end:end + 1]
        if nextchar != '"':
            if nextchar in _WS:
                end = _skip_ws(s, n, end)
                nextchar = s[end:end + 1]
            if nextchar == "}":
                end = end + 1
                return self._finish_object(pairs, end)
            if nextchar != '"':
                raise JSONDecodeError(
                    "Expecting property name enclosed in double quotes",
                    s, end)
        end = end + 1
        while True:
            key, end = _scan_string(s, n, end, self.strict)
            if s[end:end + 1] != ":":
                end = _skip_ws(s, n, end)
                if s[end:end + 1] != ":":
                    raise JSONDecodeError("Expecting ':' delimiter", s, end)
            end = _skip_ws(s, n, end + 1)
            value, end = self._scan_once(s, n, end)
            pairs.append((key, value))
            nextchar = s[end:end + 1]
            if nextchar in _WS:
                end = _skip_ws(s, n, end)
                nextchar = s[end:end + 1]
            end = end + 1
            if nextchar == "}":
                break
            if nextchar != ",":
                raise JSONDecodeError("Expecting ',' delimiter", s, end - 1)
            comma_idx = end - 1
            end = _skip_ws(s, n, end)
            nextchar = s[end:end + 1]
            end = end + 1
            if nextchar != '"':
                if nextchar == "}":
                    raise JSONDecodeError(
                        "Illegal trailing comma before end of object",
                        s, comma_idx)
                raise JSONDecodeError(
                    "Expecting property name enclosed in double quotes",
                    s, end - 1)
        return self._finish_object(pairs, end)

    def _finish_object(self, pairs, end):
        # `object_pairs_hook` WINS OVER `object_hook` when both are given --
        # CPython's own stated priority -- and gets the ORDERED PAIRS rather
        # than a dict, which is the one thing a dict cannot preserve when two
        # keys repeat.
        if self.object_pairs_hook is not None:
            return self.object_pairs_hook(pairs), end
        result = dict(pairs)
        if self.object_hook is not None:
            result = self.object_hook(result)
        return result, end

    def _scan_array(self, s, n, end):
        values = []
        nextchar = s[end:end + 1]
        if nextchar in _WS:
            end = _skip_ws(s, n, end)
            nextchar = s[end:end + 1]
        if nextchar == "]":
            return values, end + 1
        while True:
            value, end = self._scan_once(s, n, end)
            values.append(value)
            nextchar = s[end:end + 1]
            if nextchar in _WS:
                end = _skip_ws(s, n, end)
                nextchar = s[end:end + 1]
            end = end + 1
            if nextchar == "]":
                break
            if nextchar != ",":
                raise JSONDecodeError("Expecting ',' delimiter", s, end - 1)
            comma_idx = end - 1
            end = _skip_ws(s, n, end)
            nextchar = s[end:end + 1]
            if nextchar == "]":
                raise JSONDecodeError(
                    "Illegal trailing comma before end of array",
                    s, comma_idx)
        return values, end


# ── the encoder ──────────────────────────────────────────────────────────────

def _build_escape_map():
    table = {
        "\\": "\\\\", '"': '\\"', "\b": "\\b", "\f": "\\f",
        "\n": "\\n", "\r": "\\r", "\t": "\\t",
    }
    for i in range(0x20):
        ch = chr(i)
        if ch not in table:
            table[ch] = "\\u%04x" % (i,)
    return table


_ESCAPE_MAP = _build_escape_map()
_INF = float("inf")
_NEGINF = float("-inf")

#: Answered by `_encode_key` for a key `skipkeys` says to drop -- a sentinel
#: rather than `None`, because `None` is ITSELF a legal (JSON `null`) key.
_SKIP = object()


def _encode_string(s, ensure_ascii):
    """A JSON string literal for `s`. `ensure_ascii` decides only what
    happens to a character already past the two-character escapes and the
    C0 controls above -- those are escaped either way."""
    out = ['"']
    for ch in s:
        got = _ESCAPE_MAP.get(ch)
        if got is not None:
            out.append(got)
            continue
        code = ord(ch)
        if ensure_ascii and code > 0x7e:
            if code < 0x10000:
                out.append("\\u%04x" % (code,))
            else:
                code = code - 0x10000
                hi = 0xd800 | ((code >> 10) & 0x3ff)
                lo = 0xdc00 | (code & 0x3ff)
                out.append("\\u%04x\\u%04x" % (hi, lo))
            continue
        out.append(ch)
    out.append('"')
    return "".join(out)


class JSONEncoder:
    """Walks `o` and yields the JSON text a piece at a time.

    A GENERATOR RATHER THAN A STRING BUILDER, because `dump(obj, fp)` writing
    each piece as it is produced is the reason `iterencode` exists at all --
    `encode` is `"".join(self.iterencode(o))` and nothing more.
    """

    item_separator = ", "
    key_separator = ": "

    def __init__(self, *, skipkeys=False, ensure_ascii=True,
                 check_circular=True, allow_nan=True, sort_keys=False,
                 indent=None, separators=None, default=None):
        self.skipkeys = skipkeys
        self.ensure_ascii = ensure_ascii
        self.check_circular = check_circular
        self.allow_nan = allow_nan
        self.sort_keys = sort_keys
        self.indent = indent
        if separators is not None:
            self.item_separator, self.key_separator = separators
        elif indent is not None:
            # THE DEFAULT COMPACTS ONLY THE ITEM SEPARATOR when indenting:
            # `": "` stays, because the key and its value are on the same
            # line either way and the trailing space there is not clutter
            # the way one before a newline would be.
            self.item_separator = ","
        if default is not None:
            self.default = default

    def default(self, o):
        # `type(o)` AND NOT `o.__class__`: CPython's own `default` writes
        # `o.__class__.__name__`, and a builtin value under uasm has no
        # `__class__` attribute at all -- `dataclasses._class_of` found the
        # same gap first. `type()` is CPython's OWN fallback for exactly this
        # case and answers identically unless `o` overrides `__class__`,
        # which nothing serializable here would have a reason to do.
        raise TypeError("Object of type %s is not JSON serializable" %
                         (type(o).__name__,))

    def encode(self, o):
        return "".join(self.iterencode(o))

    def iterencode(self, o):
        markers = {} if self.check_circular else None
        if isinstance(self.indent, str):
            indent = self.indent
        elif self.indent is not None:
            indent = " " * self.indent
        else:
            indent = None
        return self._encode(o, 0, markers, indent)

    def _float_repr(self, o):
        # `o != o` IS THE NaN TEST: the one float that is never equal to
        # itself, and the reason this is written as a comparison rather than
        # a name this runtime may not have.
        if o != o:
            text = "NaN"
        elif o == _INF:
            text = "Infinity"
        elif o == _NEGINF:
            text = "-Infinity"
        else:
            return repr(o)
        if not self.allow_nan:
            raise ValueError(
                "Out of range float values are not JSON compliant: " +
                repr(o))
        return text

    def _encode_key(self, key):
        """A dict key as JSON text, or `_SKIP`. JSON keys are always
        strings, so JavaScript's other three JSON-able scalars are spelled
        out here rather than refused -- CPython allows exactly these four."""
        if isinstance(key, str):
            return key
        if key is True:
            return "true"
        if key is False:
            return "false"
        if key is None:
            return "null"
        if isinstance(key, float):
            return self._float_repr(key)
        if isinstance(key, int):
            return str(key)
        if self.skipkeys:
            return _SKIP
        # `type(key)`, same reason as `default` above: a plain `tuple` (the
        # obvious wrong key to test this with) has no `__class__` attribute
        # under uasm, and `AttributeError` from inside a `TypeError`
        # message is not the refusal this line means to give.
        raise TypeError(
            "keys must be str, int, float, bool or None, not %s" %
            (type(key).__name__,))

    def _encode(self, o, level, markers, indent):
        # `bool` BEFORE `int`: `True` and `False` ARE `int` in Python, and a
        # kind check ordered the other way would print them as `1`/`0`.
        if isinstance(o, str):
            yield _encode_string(o, self.ensure_ascii)
        elif o is None:
            yield "null"
        elif o is True:
            yield "true"
        elif o is False:
            yield "false"
        elif isinstance(o, int):
            yield str(o)
        elif isinstance(o, float):
            yield self._float_repr(o)
        elif isinstance(o, (list, tuple)):
            for piece in self._encode_list(o, level, markers, indent):
                yield piece
        elif isinstance(o, dict):
            for piece in self._encode_dict(o, level, markers, indent):
                yield piece
        else:
            marker_id = None
            if markers is not None:
                marker_id = id(o)
                if marker_id in markers:
                    raise ValueError("Circular reference detected")
                markers[marker_id] = o
            newobj = self.default(o)
            for piece in self._encode(newobj, level, markers, indent):
                yield piece
            if markers is not None:
                del markers[marker_id]

    def _encode_list(self, lst, level, markers, indent):
        if not lst:
            yield "[]"
            return
        marker_id = None
        if markers is not None:
            marker_id = id(lst)
            if marker_id in markers:
                raise ValueError("Circular reference detected")
            markers[marker_id] = lst
        yield "["
        if indent is not None:
            level = level + 1
            newline_indent = "\n" + indent * level
            separator = self.item_separator + newline_indent
            yield newline_indent
        else:
            newline_indent = None
            separator = self.item_separator
        first = True
        for value in lst:
            if first:
                first = False
            else:
                yield separator
            for piece in self._encode(value, level, markers, indent):
                yield piece
        if newline_indent is not None:
            level = level - 1
            yield "\n" + indent * level
        yield "]"
        if markers is not None:
            del markers[marker_id]

    def _encode_dict(self, dct, level, markers, indent):
        if not dct:
            yield "{}"
            return
        marker_id = None
        if markers is not None:
            marker_id = id(dct)
            if marker_id in markers:
                raise ValueError("Circular reference detected")
            markers[marker_id] = dct
        yield "{"
        if indent is not None:
            level = level + 1
            newline_indent = "\n" + indent * level
            item_separator = self.item_separator + newline_indent
        else:
            newline_indent = None
            item_separator = self.item_separator
        first = True
        items = sorted(dct.items()) if self.sort_keys else dct.items()
        for key, value in items:
            key = self._encode_key(key)
            if key is _SKIP:
                continue
            if first:
                first = False
                if newline_indent is not None:
                    yield newline_indent
            else:
                yield item_separator
            yield _encode_string(key, self.ensure_ascii)
            yield self.key_separator
            for piece in self._encode(value, level, markers, indent):
                yield piece
        # A DICT WHOSE KEYS WERE ALL SKIPPED never set `first` False, so it
        # closes with no indent written for it either -- an object that
        # printed no members prints no formatting for them, same as `{}`.
        if not first and newline_indent is not None:
            level = level - 1
            yield "\n" + indent * level
        yield "}"
        if markers is not None:
            del markers[marker_id]


# ── the module surface ──────────────────────────────────────────────────────

def dumps(obj, *, skipkeys=False, ensure_ascii=True, check_circular=True,
          allow_nan=True, indent=None, separators=None, default=None,
          sort_keys=False):
    return JSONEncoder(
        skipkeys=skipkeys, ensure_ascii=ensure_ascii,
        check_circular=check_circular, allow_nan=allow_nan, indent=indent,
        separators=separators, default=default,
        sort_keys=sort_keys).encode(obj)


def dump(obj, fp, *, skipkeys=False, ensure_ascii=True, check_circular=True,
         allow_nan=True, indent=None, separators=None, default=None,
         sort_keys=False):
    encoder = JSONEncoder(
        skipkeys=skipkeys, ensure_ascii=ensure_ascii,
        check_circular=check_circular, allow_nan=allow_nan, indent=indent,
        separators=separators, default=default, sort_keys=sort_keys)
    for chunk in encoder.iterencode(obj):
        fp.write(chunk)


def loads(s, *, parse_float=None, parse_int=None, parse_constant=None,
          object_hook=None, object_pairs_hook=None):
    if isinstance(s, str):
        if s[:1] == "\ufeff":
            raise JSONDecodeError(
                "Unexpected UTF-8 BOM (decode using utf-8-sig)", s, 0)
    elif isinstance(s, (bytes, bytearray)):
        s = s.decode("utf-8")
    else:
        raise TypeError(
            "the JSON object must be str, bytes or bytearray, not %s" %
            (type(s).__name__,))
    decoder = JSONDecoder(object_hook=object_hook, parse_float=parse_float,
                           parse_int=parse_int, parse_constant=parse_constant,
                           object_pairs_hook=object_pairs_hook)
    return decoder.decode(s)


def load(fp, *, parse_float=None, parse_int=None, parse_constant=None,
         object_hook=None, object_pairs_hook=None):
    return loads(fp.read(), parse_float=parse_float, parse_int=parse_int,
                 parse_constant=parse_constant, object_hook=object_hook,
                 object_pairs_hook=object_pairs_hook)
