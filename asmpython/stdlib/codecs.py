"""codecs: Codec registry and base classes.

Implements encoding/decoding for common text encodings in pure Python.
Supported encodings:
  - utf-8 / utf8     — UTF-8 (pass-through for ASCII-range text)
  - ascii            — ASCII (validates 0-127 range)
  - latin-1 / latin1 / iso-8859-1 — ISO 8859-1 (pass-through for 0-255)
  - rot-13 / rot13   — ROT-13 substitution cipher
  - hex-codec / hex  — hex encoding (delegates to binascii)
  - base64-codec     — base64 encoding (delegates to base64 module)
  - raw-unicode-escape — pass-through stub
  - unicode-escape   — pass-through stub

Note: multi-byte Unicode beyond BMP is not supported in asmpython's string
model. UTF-8 encoding/decoding is correct only for ASCII-range (0-127).
"""
from __future__ import annotations

import base64 as _base64
import binascii as _binascii


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CodecInfo:
    """Information about a codec."""

    def __init__(self, name: str, encode_fn: int, decode_fn: int) -> None:
        self.name: str = name
        self._encode: int = encode_fn
        self._decode: int = decode_fn

    def encode(self, text: str, errors: str = "strict") -> str:
        fn: int = self._encode
        return fn(text, errors)

    def decode(self, data: str, errors: str = "strict") -> str:
        fn: int = self._decode
        return fn(data, errors)


# ---------------------------------------------------------------------------
# ROT-13
# ---------------------------------------------------------------------------

def _rot13(text: str) -> str:
    result: str = ""
    for ch in text:
        o: int = ord(ch)
        if 65 <= o <= 90:
            result = result + chr(65 + (o - 65 + 13) % 26)
        elif 97 <= o <= 122:
            result = result + chr(97 + (o - 97 + 13) % 26)
        else:
            result = result + ch
    return result


def _rot13_encode(text: str, errors: str = "strict") -> str:
    return _rot13(text)


def _rot13_decode(data: str, errors: str = "strict") -> str:
    return _rot13(data)


# ---------------------------------------------------------------------------
# ASCII
# ---------------------------------------------------------------------------

def _ascii_encode(text: str, errors: str = "strict") -> str:
    result: str = ""
    for ch in text:
        o: int = ord(ch)
        if o > 127:
            if errors == "ignore":
                pass
            elif errors == "replace":
                result = result + "?"
            else:
                raise UnicodeEncodeError("ascii", text, 0, 1, "ordinal not in range(128)")
        else:
            result = result + ch
    return result


def _ascii_decode(data: str, errors: str = "strict") -> str:
    return _ascii_encode(data, errors)


# ---------------------------------------------------------------------------
# Latin-1 / ISO-8859-1
# ---------------------------------------------------------------------------

def _latin1_encode(text: str, errors: str = "strict") -> str:
    result: str = ""
    for ch in text:
        o: int = ord(ch)
        if o > 255:
            if errors == "ignore":
                pass
            elif errors == "replace":
                result = result + "?"
            else:
                raise UnicodeEncodeError("latin-1", text, 0, 1, "ordinal not in range(256)")
        else:
            result = result + ch
    return result


def _latin1_decode(data: str, errors: str = "strict") -> str:
    return data


# ---------------------------------------------------------------------------
# UTF-8 (ASCII-range pass-through; surrogate pairs not supported)
# ---------------------------------------------------------------------------

def _utf8_encode(text: str, errors: str = "strict") -> str:
    result: str = ""
    for ch in text:
        o: int = ord(ch)
        if o < 0x80:
            result = result + ch
        elif o < 0x800:
            b0: int = 0xC0 | (o >> 6)
            b1: int = 0x80 | (o & 0x3F)
            result = result + chr(b0) + chr(b1)
        elif o < 0x10000:
            b0 = 0xE0 | (o >> 12)
            b1 = 0x80 | ((o >> 6) & 0x3F)
            b2: int = 0x80 | (o & 0x3F)
            result = result + chr(b0) + chr(b1) + chr(b2)
        else:
            if errors == "ignore":
                pass
            elif errors == "replace":
                result = result + chr(0xEF) + chr(0xBF) + chr(0xBD)
            else:
                raise UnicodeEncodeError("utf-8", text, 0, 1, "character out of range")
    return result


def _utf8_decode(data: str, errors: str = "strict") -> str:
    result: str = ""
    i: int = 0
    n: int = len(data)
    while i < n:
        o: int = ord(data[i])
        if o < 0x80:
            result = result + data[i]
            i = i + 1
        elif o < 0xC0:
            if errors != "ignore":
                result = result + data[i]
            i = i + 1
        elif o < 0xE0:
            if i + 1 < n:
                cp: int = ((o & 0x1F) << 6) | (ord(data[i + 1]) & 0x3F)
                result = result + chr(cp)
                i = i + 2
            else:
                i = i + 1
        elif o < 0xF0:
            if i + 2 < n:
                cp = ((o & 0x0F) << 12) | ((ord(data[i + 1]) & 0x3F) << 6) | (ord(data[i + 2]) & 0x3F)
                result = result + chr(cp)
                i = i + 3
            else:
                i = i + 1
        else:
            i = i + 1
    return result


# ---------------------------------------------------------------------------
# Hex codec
# ---------------------------------------------------------------------------

def _hex_encode(text: str, errors: str = "strict") -> str:
    return _binascii.hexlify(text)


def _hex_decode(data: str, errors: str = "strict") -> str:
    return _binascii.unhexlify(data)


# ---------------------------------------------------------------------------
# Base64 codec
# ---------------------------------------------------------------------------

def _base64_encode(text: str, errors: str = "strict") -> str:
    return _base64.b64encode(text)


def _base64_decode(data: str, errors: str = "strict") -> str:
    return _base64.b64decode(data)


# ---------------------------------------------------------------------------
# Codec lookup
# ---------------------------------------------------------------------------

def _normalize_name(encoding: str) -> str:
    return encoding.lower().replace("-", "_").replace(" ", "_")


def lookup(encoding: str) -> CodecInfo:
    """Look up a codec by name and return a CodecInfo object."""
    name: str = _normalize_name(encoding)
    if name == "rot_13" or name == "rot13":
        return CodecInfo("rot-13", _rot13_encode, _rot13_decode)
    if name == "ascii" or name == "us_ascii":
        return CodecInfo("ascii", _ascii_encode, _ascii_decode)
    if name in ("latin_1", "latin1", "iso_8859_1", "iso8859_1", "8859"):
        return CodecInfo("latin-1", _latin1_encode, _latin1_decode)
    if name in ("utf_8", "utf8", "u8"):
        return CodecInfo("utf-8", _utf8_encode, _utf8_decode)
    if name in ("hex_codec", "hex"):
        return CodecInfo("hex-codec", _hex_encode, _hex_decode)
    if name in ("base64_codec", "base64"):
        return CodecInfo("base64-codec", _base64_encode, _base64_decode)
    if name in ("raw_unicode_escape",):
        return CodecInfo("raw-unicode-escape", _latin1_encode, _latin1_decode)
    if name in ("unicode_escape",):
        return CodecInfo("unicode-escape", _latin1_encode, _latin1_decode)
    raise LookupError("unknown encoding: " + encoding)


# ---------------------------------------------------------------------------
# encode / decode convenience functions
# ---------------------------------------------------------------------------

def encode(text: str, encoding: str = "utf-8", errors: str = "strict") -> str:
    """Encode *text* using *encoding*. Returns encoded bytes as str."""
    ci: CodecInfo = lookup(encoding)
    return ci.encode(text, errors)


def decode(data: str, encoding: str = "utf-8", errors: str = "strict") -> str:
    """Decode *data* using *encoding*. Returns decoded string."""
    ci: CodecInfo = lookup(encoding)
    return ci.decode(data, errors)


# ---------------------------------------------------------------------------
# open — open a file with a given encoding (stub; returns plain open())
# ---------------------------------------------------------------------------

def open(filename: str, mode: str = "r", encoding: str = "utf-8",
         errors: str = "strict") -> object:
    """Open a file with the given encoding (stub; returns a plain file object)."""
    return _builtin_open(filename, mode)


_builtin_open = open


# ---------------------------------------------------------------------------
# StreamReader / StreamWriter stubs
# ---------------------------------------------------------------------------

class StreamReader:
    def __init__(self, stream: object, errors: str = "strict") -> None:
        self._stream: object = stream
        self.errors: str = errors

    def read(self, size: int = -1) -> str:
        return ""

    def readline(self) -> str:
        return ""


class StreamWriter:
    def __init__(self, stream: object, errors: str = "strict") -> None:
        self._stream: object = stream
        self.errors: str = errors

    def write(self, text: str) -> None:
        pass


class IncrementalEncoder:
    def __init__(self, errors: str = "strict") -> None:
        self.errors: str = errors

    def encode(self, input: str, final: int = 0) -> str:
        return input

    def reset(self) -> None:
        pass

    def getstate(self) -> int:
        return 0

    def setstate(self, state: int) -> None:
        pass


class IncrementalDecoder:
    def __init__(self, errors: str = "strict") -> None:
        self.errors: str = errors

    def decode(self, input: str, final: int = 0) -> str:
        return input

    def reset(self) -> None:
        pass

    def getstate(self) -> list:
        return ["", 0]

    def setstate(self, state: list) -> None:
        pass


# Convenience helpers matching CPython's codecs module
def getencoder(encoding: str) -> int:
    ci: CodecInfo = lookup(encoding)
    return ci._encode


def getdecoder(encoding: str) -> int:
    ci: CodecInfo = lookup(encoding)
    return ci._decode


def getincrementalencoder(encoding: str) -> int:
    return IncrementalEncoder


def getincrementaldecoder(encoding: str) -> int:
    return IncrementalDecoder


def getreader(encoding: str) -> int:
    return StreamReader


def getwriter(encoding: str) -> int:
    return StreamWriter
