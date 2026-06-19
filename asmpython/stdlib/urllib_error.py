"""urllib.error: exception classes for urllib.request.

Accessed as::

    from urllib.error import URLError, HTTPError, ContentTooShortError
"""
from __future__ import annotations


class URLError(Exception):
    """Raised when urlopen() cannot open the URL."""

    def __init__(self, reason: str = "") -> None:
        self.reason: str = reason

    def __str__(self) -> str:
        return "URLError: " + self.reason


class HTTPError(URLError):
    """Raised when the server returns an HTTP error response."""

    def __init__(self, url: str = "", code: int = 0,
                 msg: str = "", hdrs: str = "", fp: int = 0) -> None:
        self.url: str = url
        self.code: int = code
        self.msg: str = msg
        self.hdrs: str = hdrs
        self.reason: str = msg
        self.filename: str = url

    def __str__(self) -> str:
        return "HTTP Error " + str(self.code) + ": " + self.msg

    def read(self) -> str:
        return ""


class ContentTooShortError(URLError):
    """Raised by urlretrieve() when server sends less data than Content-Length."""

    def __init__(self, msg: str = "", content: str = "") -> None:
        self.reason: str = msg
        self.content: str = content

    def __str__(self) -> str:
        return "ContentTooShortError: " + self.reason
