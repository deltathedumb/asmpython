"""locale module: internationalization services.

In asmpython, locale is a stub that reports a fixed C/POSIX locale.
All formatting functions use ASCII/English conventions.
"""
from __future__ import annotations


LC_ALL: int = 6
LC_COLLATE: int = 1
LC_CTYPE: int = 0
LC_MESSAGES: int = 5
LC_MONETARY: int = 3
LC_NUMERIC: int = 4
LC_TIME: int = 2

CHAR_MAX: int = 127


def setlocale(category: int, locale: str = "") -> str:
    """Set or query the locale (stub, always returns 'C')."""
    return "C"


def getlocale(category: int = 0) -> list:
    """Return current locale (stub, returns ['en_US', 'UTF-8'])."""
    result: list = []
    result.append("en_US")
    result.append("UTF-8")
    return result


def getdefaultlocale() -> list:
    """Return default locale (stub)."""
    result: list = []
    result.append("en_US")
    result.append("UTF-8")
    return result


def getencoding() -> str:
    """Return preferred encoding (stub, always 'UTF-8')."""
    return "UTF-8"


def getpreferredencoding(do_setlocale: int = 1) -> str:
    """Return preferred encoding (stub, always 'UTF-8')."""
    return "UTF-8"


def format_string(fmt: str, val: float, grouping: int = 0,
                  monetary: int = 0) -> str:
    """Format val according to format string (stub, uses str())."""
    return str(val)


def currency(val: float, symbol: int = 1, grouping: int = 0,
             international: int = 0) -> str:
    """Format val as currency string (stub)."""
    if val < 0.0:
        return "-$" + str(-val)
    return "$" + str(val)


def str_to_float(string: str) -> float:
    """Convert string to float (stub, uses float())."""
    return float(string)


def atof(string: str) -> float:
    """Parse string as a float (stub)."""
    return float(string)


def atoi(string: str) -> int:
    """Parse string as an int (stub)."""
    result: int = 0
    i: int = 0
    neg: int = 0
    s: str = string
    while i < len(s) and (s[i] == " " or s[i] == "\t"):
        i = i + 1
    if i < len(s) and s[i] == "-":
        neg = 1
        i = i + 1
    while i < len(s) and s[i] >= "0" and s[i] <= "9":
        result = result * 10 + ord(s[i]) - 48
        i = i + 1
    if neg == 1:
        result = -result
    return result


def localeconv() -> int:
    """Return locale-specific numeric/monetary information (stub, returns 0)."""
    return 0


def resetlocale(category: int = 6) -> None:
    """Reset locale to default (no-op)."""
    pass


def normalize(localename: str) -> str:
    """Normalize a locale code (stub, returns input)."""
    return localename


def locale_alias() -> int:
    """Return locale alias dict (stub, returns 0)."""
    return 0


def locale_encoding_alias() -> int:
    """Return locale encoding alias dict (stub, returns 0)."""
    return 0


class Error(Exception):
    """Exception for locale errors."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "locale.Error: " + self.msg
