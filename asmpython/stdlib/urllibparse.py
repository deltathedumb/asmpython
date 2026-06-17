"""urllib.parse module: URL parsing and encoding.

Implements the most commonly used parts of urllib.parse:
  quote, quote_plus, unquote, unquote_plus, urlencode,
  urlparse (-> ParseResult), urlunparse, urljoin,
  parse_qs, parse_qsl, urlsplit.
"""
from __future__ import annotations


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
            else:
                b3: int = 0xE0 | (code >> 12)
                b4: int = 0x80 | ((code >> 6) & 0x3F)
                b5: int = 0x80 | (code & 0x3F)
                result = result + "%" + _hex_digit(b3 >> 4) + _hex_digit(b3 & 0xF)
                result = result + "%" + _hex_digit(b4 >> 4) + _hex_digit(b4 & 0xF)
                result = result + "%" + _hex_digit(b5 >> 4) + _hex_digit(b5 & 0xF)
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
    """Replace %XX escapes with their single-character equivalent."""
    result: str = ""
    i: int = 0
    while i < len(string):
        c: str = string[i]
        if c == "%" and i + 2 < len(string):
            hi: int = _hex_val(string[i + 1])
            lo: int = _hex_val(string[i + 2])
            result = result + chr(hi * 16 + lo)
            i = i + 3
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


def urlencode(query: list, doseq: int = 0) -> str:
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


def urlparse(urlstring: str, scheme: str = "", allow_fragments: int = 1) -> ParseResult:
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
             allow_fragments: int = 1) -> SplitResult:
    """Parse URL without splitting params from path."""
    pr: ParseResult = urlparse(urlstring, scheme, allow_fragments)
    path_params: str = pr.path
    if len(pr.params) > 0:
        path_params = path_params + ";" + pr.params
    return SplitResult(pr.scheme, pr.netloc, path_params, pr.query, pr.fragment)


def parse_qsl(qs: str, keep_blank_values: int = 0) -> list:
    """Parse a query string, returning list of [key, value] pairs."""
    result: list = []
    if len(qs) == 0:
        return result
    i: int = 0
    while i <= len(qs):
        amp_idx: int = len(qs)
        j: int = i
        while j < len(qs):
            if qs[j] == "&" or qs[j] == ";":
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
        if len(val) > 0 or keep_blank_values == 1:
            result.append([key, val])
    return result


def urljoin(base: str, url: str, allow_fragments: int = 1) -> str:
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
    return urlunparse(ParseResult(scheme, netloc, path, up.params, query, fragment))


def parse_qs(qs: str, keep_blank_values: int = 0,
             strict_parsing: int = 0) -> list:
    """Parse a query string given as a string.

    Returns a list of [key, value] pairs (one per unique key).
    CPython returns a dict; we return a list of pairs since asmpython
    dicts must have homogeneous value types.
    """
    pairs: list = parse_qsl(qs, keep_blank_values)
    result: list = []
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
