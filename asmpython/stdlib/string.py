"""string module: common string constants and helper functions.

Matches CPython's `string` module constants exactly.
"""
from __future__ import annotations


ascii_lowercase: str = "abcdefghijklmnopqrstuvwxyz"
ascii_uppercase: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ascii_letters:   str = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
digits:          str = "0123456789"
hexdigits:       str = "0123456789abcdefABCDEF"
octdigits:       str = "01234567"
punctuation:     str = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~"
whitespace:      str = " \t\n\r"
printable:       str = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ \t\n\r"


def capwords(s: str, sep: str = " ") -> str:
    """Split the argument into words using sep, capitalize each word, and join."""
    words = s.split(sep)
    result: list[str] = []
    for w in words:
        result.append(w.capitalize())
    return sep.join(result)
