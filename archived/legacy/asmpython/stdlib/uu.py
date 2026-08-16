"""uu: encode and decode files in uuencode format.

Uuencode encodes binary data using only printable ASCII characters, with
each group of 3 bytes becoming 4 characters. This module is deprecated in
CPython 3.11+ but is implemented here for compatibility.

Usage::

    import uu

    # Encode a file
    uu.encode("input.bin", "output.uu", name="input.bin", mode=0o644)

    # Decode a file
    uu.decode("input.uu", "output.bin")
"""
from __future__ import annotations

import os as _os
import binascii as _binascii

_UU_CHARS: str = "`!\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_"


class Error(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


def _uu_encode_line(data: str) -> str:
    """Encode up to 45 bytes as a uuencode line."""
    n: int = len(data)
    if n > 45:
        data = data[:45]
        n = 45
    # Length character
    result: str = _UU_CHARS[n]
    i: int = 0
    while i < n:
        b0: int = ord(data[i]) & 0xFF
        b1: int = ord(data[i + 1]) & 0xFF if i + 1 < n else 0
        b2: int = ord(data[i + 2]) & 0xFF if i + 2 < n else 0
        # Pack 3 bytes into 4 6-bit groups
        c0: int = (b0 >> 2) & 0x3F
        c1: int = ((b0 << 4) | (b1 >> 4)) & 0x3F
        c2: int = ((b1 << 2) | (b2 >> 6)) & 0x3F
        c3: int = b2 & 0x3F
        result = result + _UU_CHARS[c0] + _UU_CHARS[c1] + _UU_CHARS[c2] + _UU_CHARS[c3]
        i = i + 3
    return result


def _uu_decode_line(line: str) -> str:
    """Decode a single uuencode data line."""
    if not line:
        return ""
    length_char: str = line[0]
    if length_char == "`" or length_char == " ":
        return ""
    n: int = ord(length_char) - ord(" ")
    if n <= 0:
        return ""
    result: str = ""
    i: int = 1
    while i + 3 < len(line):
        c0: int = ord(line[i]) - ord(" ")
        c1: int = ord(line[i + 1]) - ord(" ")
        c2: int = ord(line[i + 2]) - ord(" ")
        c3: int = ord(line[i + 3]) - ord(" ")
        if c0 < 0:
            c0 = 0
        if c1 < 0:
            c1 = 0
        if c2 < 0:
            c2 = 0
        if c3 < 0:
            c3 = 0
        b0: int = ((c0 << 2) | (c1 >> 4)) & 0xFF
        b1: int = ((c1 << 4) | (c2 >> 2)) & 0xFF
        b2: int = ((c2 << 6) | c3) & 0xFF
        if len(result) < n:
            result = result + chr(b0)
        if len(result) < n:
            result = result + chr(b1)
        if len(result) < n:
            result = result + chr(b2)
        i = i + 4
    return result


def encode(in_file: str, out_file: str, name: str = "",
           mode: int = 0o644, backtick: int = 0) -> None:
    """Encode *in_file* to *out_file* in uuencode format."""
    if not name:
        name = _os.path.basename(in_file)
    f = open(in_file, "r")
    data: str = f.read()
    f.close()
    lines: list = []
    lines.append("begin " + oct(mode)[2:] + " " + name)
    i: int = 0
    n: int = len(data)
    while i < n:
        chunk: str = data[i:i + 45]
        lines.append(_uu_encode_line(chunk))
        i = i + 45
    if backtick:
        lines.append("`")
    else:
        lines.append(" ")
    lines.append("end")
    f = open(out_file, "w")
    f.write("\n".join(lines))
    f.close()


def decode(in_file: str, out_file: str = "", mode: int = 0,
           quiet: int = 0) -> None:
    """Decode *in_file* from uuencode format to *out_file*."""
    f = open(in_file, "r")
    content: str = f.read()
    f.close()
    lines: list = content.split("\n")
    idx: int = 0
    name: str = ""
    file_mode: int = 0o644
    # Find 'begin' line
    while idx < len(lines):
        line: str = lines[idx]
        if line.startswith("begin"):
            parts: list = line.split(" ", 2)
            if len(parts) >= 3:
                file_mode = int(parts[1], 8)
                name = parts[2]
            idx = idx + 1
            break
        idx = idx + 1
    if not out_file:
        out_file = name
    if not out_file:
        raise Error("no output file specified")
    data: str = ""
    while idx < len(lines):
        line = lines[idx]
        idx = idx + 1
        if line in ("end", ""):
            break
        data = data + _uu_decode_line(line)
    f = open(out_file, "w")
    f.write(data)
    f.close()
