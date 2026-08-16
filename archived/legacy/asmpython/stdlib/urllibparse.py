"""urllib.parse module: URL parsing and encoding.

Implements the most commonly used parts of urllib.parse:
  quote, quote_plus, unquote, unquote_plus, urlencode,
  urlparse (-> ParseResult), urlunparse, urljoin,
  parse_qs, parse_qsl, urlsplit.
"""
from __future__ import annotations

#: Schemes whose URLs support relative references, a netloc, params, a query,
#: or a fragment. CPython exposes these and `urljoin` is defined in terms of
#: them, so they are part of the interface, not an implementation detail.
uses_relative: list = [
    "", "ftp", "http", "gopher", "nntp", "imap", "wais", "file", "https",
    "shttp", "mms", "prospero", "rtsp", "rtspu", "sftp", "svn", "svn+ssh",
    "ws", "wss",
]
uses_netloc: list = [
    "", "ftp", "http", "gopher", "nntp", "telnet", "imap", "wais", "file",
    "mms", "https", "shttp", "snews", "prospero", "rtsp", "rtspu", "rsync",
    "svn", "svn+ssh", "sftp", "nfs", "git", "git+ssh", "ws", "wss", "itms-services",
]
uses_params: list = [
    "", "ftp", "hdl", "prospero", "http", "imap", "https", "shttp", "rtsp",
    "rtspu", "sip", "sips", "mms", "sftp", "tel",
]
uses_fragment: list = [
    "", "ftp", "hdl", "http", "gopher", "news", "nntp", "wais", "https",
    "shttp", "snews", "file", "prospero",
]
uses_query: list = [
    "", "http", "wais", "imap", "https", "shttp", "mms", "gopher", "rtsp",
    "rtspu", "sip", "sips",
]

#: Characters legal in a scheme name, per RFC 3986.
scheme_chars: str = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-."
)



_HEX: str = "0123456789ABCDEF"
_SAFE_CHARS: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"


def _is_safe(c: str, safe: str) -> int:
    i: int = 0
    while i < len(_SAFE_CHARS):
        if _SAFE_CHARS[i] == c:
            return 1
        i = i + 1
    j: int = 0
    while j < len(safe):
        if safe[j] == c:
            return 1
        j = j + 1
    return 0


def _hex_digit(n: int) -> str:
    if n < 10:
        return chr(48 + n)
    return chr(55 + n)


def _hex_val(c: str) -> int:
    o: int = ord(c)
    if o >= 48 and o <= 57:
        return o - 48
    if o >= 65 and o <= 70:
        return o - 55
    if o >= 97 and o <= 102:
        return o - 87
    return 0


def _utf8_decode(data: list[int]) -> str:
    """Decode a UTF-8 byte run, substituting U+FFFD for anything malformed."""
    out: str = ""
    i: int = 0
    n: int = len(data)
    while i < n:
        b: int = data[i]
        if b < 0x80:
            out = out + chr(b)
            i = i + 1
        elif b & 0xE0 == 0xC0 and i + 1 < n:
            out = out + chr(((b & 0x1F) << 6) | (data[i + 1] & 0x3F))
            i = i + 2
        elif b & 0xF0 == 0xE0 and i + 2 < n:
            out = out + chr(
                ((b & 0x0F) << 12) | ((data[i + 1] & 0x3F) << 6) | (data[i + 2] & 0x3F)
            )
            i = i + 3
        elif b & 0xF8 == 0xF0 and i + 3 < n:
            out = out + chr(
                ((b & 0x07) << 18)
                | ((data[i + 1] & 0x3F) << 12)
                | ((data[i + 2] & 0x3F) << 6)
                | (data[i + 3] & 0x3F)
            )
            i = i + 4
        else:
            out = out + chr(0xFFFD)
            i = i + 1
    return out


def quote(string: str, safe: str = "/", encoding: str = "utf-8") -> str:
    """Percent-encode string, leaving safe chars unencoded."""
    result: str = ""
    i: int = 0
    while i < len(string):
        c: str = string[i]
        if _is_safe(c, safe) == 1:
            result = result + c
        else:
            code: int = ord(c)
            if code < 128:
                result = result + "%" + _hex_digit(code >> 4) + _hex_digit(code & 0xF)
            elif code < 2048:
                b1: int = 0xC0 | (code >> 6)
                b2: int = 0x80 | (code & 0x3F)
                result = result + "%" + _hex_digit(b1 >> 4) + _hex_digit(b1 & 0xF)
                result = result + "%" + _hex_digit(b2 >> 4) + _hex_digit(b2 & 0xF)
            elif code < 65536:
                b3: int = 0xE0 | (code >> 12)
                b4: int = 0x80 | ((code >> 6) & 0x3F)
                b5: int = 0x80 | (code & 0x3F)
                result = result + "%" + _hex_digit(b3 >> 4) + _hex_digit(b3 & 0xF)
                result = result + "%" + _hex_digit(b4 >> 4) + _hex_digit(b4 & 0xF)
                result = result + "%" + _hex_digit(b5 >> 4) + _hex_digit(b5 & 0xF)
            else:
                # Astral plane: four bytes. Without this branch the 3-byte form
                # was used for everything above U+07FF, so U+1F600 encoded as
                # 0xE0 | (0x1F600 >> 12) == 0xFF -- not even valid UTF-8.
                b6: int = 0xF0 | (code >> 18)
                b7: int = 0x80 | ((code >> 12) & 0x3F)
                b8: int = 0x80 | ((code >> 6) & 0x3F)
                b9: int = 0x80 | (code & 0x3F)
                result = result + "%" + _hex_digit(b6 >> 4) + _hex_digit(b6 & 0xF)
                result = result + "%" + _hex_digit(b7 >> 4) + _hex_digit(b7 & 0xF)
                result = result + "%" + _hex_digit(b8 >> 4) + _hex_digit(b8 & 0xF)
                result = result + "%" + _hex_digit(b9 >> 4) + _hex_digit(b9 & 0xF)
        i = i + 1
    return result


def quote_plus(string: str, safe: str = "", encoding: str = "utf-8") -> str:
    """Like quote() but replace spaces with '+'."""
    result: str = quote(string, safe + " ", encoding)
    out: str = ""
    i: int = 0
    while i < len(result):
        if result[i] == " ":
            out = out + "+"
        else:
            out = out + result[i]
        i = i + 1
    return out


def unquote(string: str, encoding: str = "utf-8") -> str:
    """Replace %XX escapes with the characters they encode.

    Escapes are gathered into a byte run and decoded as UTF-8 together. Doing
    it one escape at a time yields one character per BYTE, so any non-ASCII
    character came back as mojibake.
    """
    result: str = ""
    i: int = 0
    n: int = len(string)
    while i < n:
        c: str = string[i]
        if c == "%" and i + 2 < n:
            run: list[int] = []
            while i + 2 < n and string[i] == "%":
                hi: int = _hex_val(string[i + 1])
                lo: int = _hex_val(string[i + 2])
                if hi < 0 or lo < 0:
                    break
                run.append(hi * 16 + lo)
                i = i + 3
            result = result + _utf8_decode(run)
        else:
            result = result + c
            i = i + 1
    return result


def unquote_plus(string: str, encoding: str = "utf-8") -> str:
    """Like unquote() but also replace '+' with space."""
    replaced: str = ""
    i: int = 0
    while i < len(string):
        if string[i] == "+":
            replaced = replaced + " "
        else:
            replaced = replaced + string[i]
        i = i + 1
    return unquote(replaced, encoding)


def urlencode(query: list, doseq: bool = False) -> str:
    """Encode a list of (key, value) pairs as a URL query string."""
    parts: list = []
    i: int = 0
    while i < len(query):
        pair: list = query[i]
        k: str = quote_plus(str(pair[0]))
        v: str = quote_plus(str(pair[1]))
        parts.append(k + "=" + v)
        i = i + 1
    result: str = ""
    j: int = 0
    while j < len(parts):
        if j > 0:
            result = result + "&"
        result = result + parts[j]
        j = j + 1
    return result


class ParseResult:
    """Result of urlparse()."""

    def __init__(self, scheme: str, netloc: str, path: str,
                 params: str, query: str, fragment: str) -> None:
        self.scheme: str = scheme
        self.netloc: str = netloc
        self.path: str = path
        self.params: str = params
        self.query: str = query
        self.fragment: str = fragment

    def geturl(self) -> str:
        return urlunparse(self)

    def __str__(self) -> str:
        return ("ParseResult(scheme=" + self.scheme + ", netloc=" + self.netloc +
                ", path=" + self.path + ")")


def urlparse(urlstring: str, scheme: str = "", allow_fragments: bool = True) -> ParseResult:
    """Parse a URL into 6 components."""
    rest: str = urlstring
    found_scheme: str = scheme
    found_netloc: str = ""
    found_path: str = ""
    found_params: str = ""
    found_query: str = ""
    found_fragment: str = ""

    frag_idx: int = -1
    i: int = 0
    while i < len(rest):
        if rest[i] == "#" and allow_fragments == 1:
            frag_idx = i
            break
        i = i + 1
    if frag_idx >= 0:
        found_fragment = rest[frag_idx + 1:]
        rest = rest[:frag_idx]

    query_idx: int = -1
    i = 0
    while i < len(rest):
        if rest[i] == "?":
            query_idx = i
            break
        i = i + 1
    if query_idx >= 0:
        found_query = rest[query_idx + 1:]
        rest = rest[:query_idx]

    colon_idx: int = -1
    i = 0
    while i < len(rest):
        if rest[i] == ":":
            colon_idx = i
            break
        i = i + 1
    if colon_idx > 0 and colon_idx + 2 < len(rest) and rest[colon_idx + 1] == "/" and rest[colon_idx + 2] == "/":
        found_scheme = rest[:colon_idx].lower()
        rest = rest[colon_idx + 3:]
        slash_idx: int = -1
        k: int = 0
        while k < len(rest):
            if rest[k] == "/":
                slash_idx = k
                break
            k = k + 1
        if slash_idx >= 0:
            found_netloc = rest[:slash_idx]
            rest = rest[slash_idx:]
        else:
            found_netloc = rest
            rest = ""
    elif colon_idx > 0:
        maybe_scheme: str = rest[:colon_idx]
        is_scheme: int = 1
        m: int = 0
        while m < len(maybe_scheme):
            mc: str = maybe_scheme[m]
            if not ((mc >= "a" and mc <= "z") or (mc >= "A" and mc <= "Z") or
                    (mc >= "0" and mc <= "9") or mc == "+" or mc == "-" or mc == "."):
                is_scheme = 0
            m = m + 1
        if is_scheme == 1:
            found_scheme = maybe_scheme.lower()
            rest = rest[colon_idx + 1:]

    # A protocol-relative URL ("//host/path") carries a netloc with no scheme.
    # CPython strips the scheme first and then treats any leading "//" as the
    # authority, so this is not specific to the scheme-less case.
    if len(found_netloc) == 0 and len(rest) >= 2 and rest[0] == "/" and rest[1] == "/":
        rest = rest[2:]
        slash_after: int = -1
        q: int = 0
        while q < len(rest):
            if rest[q] == "/":
                slash_after = q
                break
            q = q + 1
        if slash_after >= 0:
            found_netloc = rest[:slash_after]
            rest = rest[slash_after:]
        else:
            found_netloc = rest
            rest = ""

    param_idx: int = -1
    i = 0
    while i < len(rest):
        if rest[i] == ";":
            param_idx = i
            break
        i = i + 1
    if param_idx >= 0:
        found_params = rest[param_idx + 1:]
        found_path = rest[:param_idx]
    else:
        found_path = rest

    return ParseResult(found_scheme, found_netloc, found_path,
                       found_params, found_query, found_fragment)


def urlunparse(components: ParseResult) -> str:
    """Assemble URL components back into a URL string."""
    result: str = ""
    if len(components.scheme) > 0:
        result = result + components.scheme + "://"
    result = result + components.netloc + components.path
    if len(components.params) > 0:
        result = result + ";" + components.params
    if len(components.query) > 0:
        result = result + "?" + components.query
    if len(components.fragment) > 0:
        result = result + "#" + components.fragment
    return result


class SplitResult:
    """Result of urlsplit() (no params component)."""

    def __init__(self, scheme: str, netloc: str, path: str,
                 query: str, fragment: str) -> None:
        self.scheme: str = scheme
        self.netloc: str = netloc
        self.path: str = path
        self.query: str = query
        self.fragment: str = fragment

    def geturl(self) -> str:
        r: str = ""
        if len(self.scheme) > 0:
            r = r + self.scheme + "://"
        r = r + self.netloc + self.path
        if len(self.query) > 0:
            r = r + "?" + self.query
        if len(self.fragment) > 0:
            r = r + "#" + self.fragment
        return r


def urlsplit(urlstring: str, scheme: str = "",
             allow_fragments: bool = True) -> SplitResult:
    """Parse URL without splitting params from path."""
    pr: ParseResult = urlparse(urlstring, scheme, allow_fragments)
    path_params: str = pr.path
    if len(pr.params) > 0:
        path_params = path_params + ";" + pr.params
    return SplitResult(pr.scheme, pr.netloc, path_params, pr.query, pr.fragment)


def parse_qsl(qs: str, keep_blank_values: bool = False) -> list:
    """Parse a query string into a list of (key, value) tuples."""
    result: list[int] = []
    if len(qs) == 0:
        return result
    i: int = 0
    while i <= len(qs):
        amp_idx: int = len(qs)
        j: int = i
        while j < len(qs):
            if qs[j] == "&":
                amp_idx = j
                break
            j = j + 1
        part: str = qs[i:amp_idx]
        i = amp_idx + 1
        if len(part) == 0:
            continue
        eq_idx: int = -1
        k: int = 0
        while k < len(part):
            if part[k] == "=":
                eq_idx = k
                break
            k = k + 1
        if eq_idx >= 0:
            key: str = unquote_plus(part[:eq_idx])
            val: str = unquote_plus(part[eq_idx + 1:])
        else:
            key = unquote_plus(part)
            val = ""
        if len(val) > 0 or keep_blank_values:
            result.append((key, val))
    return result


def _remove_dot_segments(path: str) -> str:
    """RFC 3986 5.2.4: resolve '.' and '..' inside a path.

    Without this, urljoin("http://a/x/y", "../z") produced
    "http://a/x/../z" instead of "http://a/z".
    """
    leading = path.startswith("/")
    trailing = path.endswith("/") or path.endswith("/.") or path.endswith("/..")
    out: list[int] = []
    start = 0
    i = 0
    n = len(path)
    while i <= n:
        if i == n or path[i] == "/":
            seg = path[start:i]
            if seg == "..":
                if len(out) > 0:
                    out.pop()
            elif seg != "." and len(seg) > 0:
                out.append(seg)
            start = i + 1
        i = i + 1
    joined = "/".join(out)
    if leading:
        joined = "/" + joined
    if trailing and len(joined) > 0 and not joined.endswith("/"):
        joined = joined + "/"
    if len(joined) == 0 and leading:
        return "/"
    return joined


def urljoin(base: str, url: str, allow_fragments: bool = True) -> str:
    """Join base URL with a possibly relative URL."""
    if len(url) == 0:
        return base
    bp: ParseResult = urlparse(base, "", allow_fragments)
    up: ParseResult = urlparse(url, "", allow_fragments)
    if len(up.scheme) > 0 and up.scheme != bp.scheme:
        return url
    scheme: str = bp.scheme
    netloc: str = bp.netloc
    path: str = up.path
    query: str = up.query
    fragment: str = up.fragment
    if len(up.netloc) > 0:
        netloc = up.netloc
        return urlunparse(ParseResult(scheme, netloc, path, "", query, fragment))
    if len(path) == 0:
        path = bp.path
        if len(up.query) == 0:
            query = bp.query
    elif path[0] == "/":
        pass
    else:
        slash: int = -1
        i: int = len(bp.path) - 1
        while i >= 0:
            if bp.path[i] == "/":
                slash = i
                break
            i = i - 1
        if slash >= 0:
            path = bp.path[:slash + 1] + path
        else:
            path = "/" + path
    path = _remove_dot_segments(path)
    return urlunparse(ParseResult(scheme, netloc, path, up.params, query, fragment))


def parse_qs(qs: str, keep_blank_values: bool = False,
             strict_parsing: bool = False) -> list:
    """Parse a query string given as a string.

    Returns a list of [key, value] pairs (one per unique key).
    CPython returns a dict; we return a list of pairs since asmpython
    dicts must have homogeneous value types.
    """
    pairs: list = parse_qsl(qs, keep_blank_values)
    result: list[int] = []
    keys: list[str] = []
    vals: list = []
    for pair in pairs:
        k: str = pair[0]
        v: str = pair[1]
        found: int = 0
        i: int = 0
        while i < len(keys):
            if keys[i] == k:
                found = 1
                break
            i = i + 1
        if found == 0:
            keys.append(k)
            entry: list[str] = [k, v]
            vals.append(entry)
            result.append(entry)
        else:
            vals[i].append(v)
    return result


def urldefrag(url: str) -> list:
    """Remove any fragment from a URL, returning [defraged_url, fragment]."""
    idx: int = -1
    i: int = 0
    while i < len(url):
        if url[i] == "#":
            idx = i
            break
        i = i + 1
    if idx < 0:
        return [url, ""]
    return [url[:idx], url[idx + 1:]]
