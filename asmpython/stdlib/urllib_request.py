"""urllib.request: HTTP/HTTPS URL opener (urllib.request shim).

Implements the urlopen / Request / urlretrieve API using asmpython's
socket module. Only HTTP/1.0 GET and POST are supported; HTTPS is a
stub (no TLS in the native runtime).

Import as::

    from urllib.request import urlopen, Request, urlretrieve
"""
from __future__ import annotations

import socket as _socket


# ---------------------------------------------------------------------------
# Exceptions (re-exported from urllib.error for convenience)
# ---------------------------------------------------------------------------

class URLError(Exception):
    """Raised when urlopen cannot open the URL."""

    def __init__(self, reason: str = "") -> None:
        self.reason: str = reason

    def __str__(self) -> str:
        return "URLError: " + self.reason


class HTTPError(URLError):
    """Raised when the server returns an HTTP error code."""

    def __init__(self, url: str = "", code: int = 0,
                 msg: str = "", hdrs: str = "", fp: int = 0) -> None:
        self.url: str = url
        self.code: int = code
        self.msg: str = msg
        self.hdrs: str = hdrs
        self.reason: str = msg

    def __str__(self) -> str:
        return "HTTP Error " + str(self.code) + ": " + self.msg


class ContentTooShortError(URLError):
    """Raised by urlretrieve when downloaded less data than Content-Length."""

    def __init__(self, msg: str = "", content: str = "") -> None:
        self.reason: str = msg
        self.content: str = content


# ---------------------------------------------------------------------------
# Response object
# ---------------------------------------------------------------------------

class HTTPResponse:
    """Minimal response object returned by urlopen()."""

    def __init__(self, status: int, reason: str, headers: dict,
                 body: str) -> None:
        self.status: int = status
        self.code: int = status
        self.reason: str = reason
        self._headers: dict = headers
        self._body: str = body
        self._pos: int = 0
        self.url: str = ""

    def read(self, amt: int = -1) -> str:
        """Read and return up to *amt* bytes from the response body."""
        n: int = len(self._body)
        if self._pos >= n:
            return ""
        if amt < 0:
            result: str = self._body[self._pos:]
            self._pos = n
            return result
        end: int = self._pos + amt
        if end > n:
            end = n
        result2: str = self._body[self._pos:end]
        self._pos = end
        return result2

    def readline(self) -> str:
        """Read one line from the response body."""
        n: int = len(self._body)
        start: int = self._pos
        while self._pos < n:
            if self._body[self._pos] == "\n":
                self._pos = self._pos + 1
                return self._body[start:self._pos]
            self._pos = self._pos + 1
        return self._body[start:self._pos]

    def getheader(self, name: str, default: str = "") -> str:
        """Return the value of response header *name* (case-insensitive)."""
        lower_name: str = name.lower()
        for k in self._headers:
            if k.lower() == lower_name:
                return self._headers[k]
        return default

    def getheaders(self) -> list:
        """Return list of [name, value] header pairs."""
        result: list = []
        for k in self._headers:
            result.append([k, self._headers[k]])
        return result

    def info(self) -> dict:
        """Return the response headers dict."""
        return self._headers

    def getcode(self) -> int:
        return self.status

    def close(self) -> None:
        pass

    def __enter__(self) -> HTTPResponse:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.close()
        return 0


# ---------------------------------------------------------------------------
# Low-level HTTP/1.0 client
# ---------------------------------------------------------------------------

def _parse_url(url: str) -> list:
    """Return [scheme, host, port, path] from an http:// or https:// URL."""
    scheme: str = "http"
    rest: str = url
    if url[0:7] == "http://":
        rest = url[7:]
        scheme = "http"
    elif url[0:8] == "https://":
        rest = url[8:]
        scheme = "https"

    slash: int = -1
    i: int = 0
    while i < len(rest):
        if rest[i] == "/":
            slash = i
            break
        i = i + 1

    if slash < 0:
        host_part: str = rest
        path: str = "/"
    else:
        host_part = rest[0:slash]
        path = rest[slash:]

    colon: int = -1
    j: int = 0
    while j < len(host_part):
        if host_part[j] == ":":
            colon = j
            break
        j = j + 1

    if colon >= 0:
        host: str = host_part[0:colon]
        port: int = int(host_part[colon + 1:])
    else:
        host = host_part
        port = 443 if scheme == "https" else 80

    return [scheme, host, port, path]


def _http_request(method: str, url: str, data: str, headers: dict,
                  timeout: float) -> HTTPResponse:
    """Perform a raw HTTP/1.0 request and return an HTTPResponse."""
    parts: list = _parse_url(url)
    scheme: str = parts[0]
    host: str = parts[1]
    port: int = parts[2]
    path: str = parts[3]

    if scheme == "https":
        raise URLError("HTTPS not supported (no TLS in native runtime)")

    sock: _socket.socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    if timeout > 0.0:
        sock.settimeout(timeout)
    sock.connect(host, port)

    # Build request.
    req: str = method + " " + path + " HTTP/1.0\r\n"
    req = req + "Host: " + host + "\r\n"
    req = req + "Connection: close\r\n"
    for hk in headers:
        req = req + hk + ": " + headers[hk] + "\r\n"
    if len(data) > 0:
        req = req + "Content-Length: " + str(len(data)) + "\r\n"
    req = req + "\r\n"
    if len(data) > 0:
        req = req + data

    sock.sendall(req)

    # Read response.
    raw: str = ""
    buf: str = sock.recv(4096)
    while len(buf) > 0:
        raw = raw + buf
        buf = sock.recv(4096)
    sock.close()

    # Split status line.
    crlf: int = -1
    k: int = 0
    while k < len(raw):
        if raw[k] == "\r" and k + 1 < len(raw) and raw[k + 1] == "\n":
            crlf = k
            break
        k = k + 1

    if crlf < 0:
        raise URLError("invalid HTTP response")

    status_line: str = raw[0:crlf]
    rest2: str = raw[crlf + 2:]

    # Parse status.
    sp1: int = -1
    p: int = 0
    while p < len(status_line):
        if status_line[p] == " ":
            sp1 = p
            break
        p = p + 1
    sp2: int = -1
    p = sp1 + 1
    while p < len(status_line):
        if status_line[p] == " ":
            sp2 = p
            break
        p = p + 1
    status_code: int = 0
    reason_phrase: str = ""
    if sp1 >= 0 and sp2 >= 0:
        status_code = int(status_line[sp1 + 1:sp2])
        reason_phrase = status_line[sp2 + 1:]
    elif sp1 >= 0:
        status_code = int(status_line[sp1 + 1:])

    # Parse headers.
    resp_headers: dict = {}
    while len(rest2) > 0:
        lf_pos: int = -1
        q: int = 0
        while q < len(rest2):
            if rest2[q] == "\r" and q + 1 < len(rest2) and rest2[q + 1] == "\n":
                lf_pos = q
                break
            q = q + 1
        if lf_pos < 0:
            break
        line: str = rest2[0:lf_pos]
        rest2 = rest2[lf_pos + 2:]
        if len(line) == 0:
            break  # blank line = end of headers
        colon2: int = -1
        r: int = 0
        while r < len(line):
            if line[r] == ":":
                colon2 = r
                break
            r = r + 1
        if colon2 >= 0:
            hname: str = line[0:colon2].strip()
            hval: str = line[colon2 + 1:].strip()
            resp_headers[hname] = hval

    resp: HTTPResponse = HTTPResponse(status_code, reason_phrase,
                                      resp_headers, rest2)
    resp.url = url

    if status_code >= 400:
        raise HTTPError(url, status_code, reason_phrase, "", 0)

    return resp


# ---------------------------------------------------------------------------
# Request class
# ---------------------------------------------------------------------------

class Request:
    """Represents an HTTP request."""

    def __init__(self, url: str, data: str = "",
                 headers: dict = {}, method: str = "") -> None:
        self.full_url: str = url
        self.data: str = data
        self._headers: dict = {}
        for k in headers:
            self._headers[k] = headers[k]
        self._method: str = method

    def get_full_url(self) -> str:
        return self.full_url

    def get_method(self) -> str:
        if len(self._method) > 0:
            return self._method
        return "POST" if len(self.data) > 0 else "GET"

    def add_header(self, key: str, val: str) -> None:
        self._headers[key] = val

    def get_header(self, header_name: str, default: str = "") -> str:
        for k in self._headers:
            if k.lower() == header_name.lower():
                return self._headers[k]
        return default

    def has_header(self, header_name: str) -> int:
        for k in self._headers:
            if k.lower() == header_name.lower():
                return 1
        return 0

    def remove_header(self, header_name: str) -> None:
        for k in self._headers:
            if k.lower() == header_name.lower():
                del self._headers[k]
                return

    def header_items(self) -> list:
        result: list = []
        for k in self._headers:
            result.append([k, self._headers[k]])
        return result


# ---------------------------------------------------------------------------
# urlopen / urlretrieve
# ---------------------------------------------------------------------------

def urlopen(url: object, data: str = "", timeout: float = 30.0) -> HTTPResponse:
    """Open a URL, returning an HTTPResponse.

    *url* may be a string URL or a Request object.
    *data* (if non-empty) causes a POST request to be sent.
    """
    if isinstance(url, Request):
        req_obj: Request = url
        method: str = req_obj.get_method()
        return _http_request(method, req_obj.full_url, req_obj.data,
                             req_obj._headers, timeout)
    url_str: str = str(url)
    method2: str = "POST" if len(data) > 0 else "GET"
    return _http_request(method2, url_str, data, {}, timeout)


def urlretrieve(url: str, filename: str = "",
                reporthook: int = 0, data: str = "") -> list:
    """Retrieve a URL into a temporary location.

    Returns [filename, headers_dict].
    """
    import os
    resp: HTTPResponse = urlopen(url, data)
    body: str = resp.read()
    if len(filename) == 0:
        import tempfile
        filename = tempfile.mktemp(suffix=".tmp")
    fp: int = os.fopen(filename, "wb")
    if fp == 0:
        raise URLError("could not open " + filename + " for writing")
    os.fputs(body, fp)
    os.fclose(fp)
    return [filename, resp._headers]


def install_opener(opener: object) -> None:
    """Install an OpenerDirector (stub)."""
    pass


def build_opener(*handlers: object) -> object:
    """Build and return an OpenerDirector (stub: returns a no-op object)."""
    return 0


# ---------------------------------------------------------------------------
# urlparse convenience (re-export from urllibparse)
# ---------------------------------------------------------------------------

def pathname2url(pathname: str) -> str:
    """Convert a local path to a file:// URL path (stub)."""
    return pathname.replace("\\", "/")


def url2pathname(pathname: str) -> str:
    """Convert a file:// URL path to a local path (stub)."""
    return pathname
