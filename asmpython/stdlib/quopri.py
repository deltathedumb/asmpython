"""quopri: encode and decode MIME quoted-printable data.

Quoted-Printable encoding represents non-ASCII bytes as '=XX' where XX
is the hexadecimal byte value. Lines are limited to 76 characters (soft
line breaks use '=' at end of line). This is commonly used for email
message bodies.

Usage::

    import quopri

    encoded = quopri.encodestring("Hello =World= \xc3\xa9")
    decoded = quopri.decodestring(encoded)
"""
from __future__ import annotations

MAXLINESIZE: int = 76
HEX: str = "0123456789ABCDEF"


def _hex_byte(b: int) -> str:
    """Convert a byte value to its '=XX' quoted-printable representation."""
    return "=" + HEX[(b >> 4) & 0xF] + HEX[b & 0xF]


def _needs_encoding(c: str) -> int:
    """Return 1 if character must be encoded in QP."""
    o: int = ord(c)
    if o == 9 or o == 32:
        return 0  # TAB and SPACE are OK in body (but not at EOL)
    if 33 <= o <= 126 and c != "=":
        return 0
    return 1


def encode(inputstr: str, quotetabs: int = 0, header: int = 0) -> str:
    """Encode *inputstr* as quoted-printable. Returns encoded string."""
    result: str = ""
    for line in inputstr.split("\n"):
        encoded_line: str = ""
        for ch in line:
            o: int = ord(ch)
            if header and ch in " \t":
                encoded_line = encoded_line + _hex_byte(o)
            elif quotetabs and ch in " \t":
                encoded_line = encoded_line + _hex_byte(o)
            elif _needs_encoding(ch):
                encoded_line = encoded_line + _hex_byte(o)
            else:
                encoded_line = encoded_line + ch
        # Enforce line length limit with soft breaks
        while len(encoded_line) > MAXLINESIZE:
            result = result + encoded_line[:MAXLINESIZE - 1] + "=\n"
            encoded_line = encoded_line[MAXLINESIZE - 1:]
        # Encode trailing whitespace before newline
        if encoded_line and encoded_line[len(encoded_line) - 1] in " \t":
            last: str = encoded_line[len(encoded_line) - 1]
            encoded_line = encoded_line[:len(encoded_line) - 1] + _hex_byte(ord(last))
        result = result + encoded_line + "\n"
    # Remove trailing newline we added
    if result and result[len(result) - 1] == "\n":
        result = result[:len(result) - 1]
    return result


def encodestring(s: str, quotetabs: int = 0, header: int = 0) -> str:
    """Encode string *s* as quoted-printable."""
    return encode(s, quotetabs, header)


def decode(inputstr: str, header: int = 0) -> str:
    """Decode a quoted-printable encoded string."""
    result: str = ""
    lines: list = inputstr.split("\n")
    for line in lines:
        # Handle soft line breaks (line ends with '=')
        if line.endswith("="):
            line = line[:len(line) - 1]
            end_nl: int = 0
        else:
            end_nl: int = 1
        i: int = 0
        n: int = len(line)
        while i < n:
            c: str = line[i]
            if c == "=" and i + 2 < n:
                h0: str = line[i + 1].upper()
                h1: str = line[i + 2].upper()
                if h0 in "0123456789ABCDEF" and h1 in "0123456789ABCDEF":
                    v0: int = int(h0, 16) if h0.isdigit() else ord(h0) - ord("A") + 10
                    v1: int = int(h1, 16) if h1.isdigit() else ord(h1) - ord("A") + 10
                    # Parse hex manually to avoid calling int(x, 16)
                    hx: str = line[i + 1:i + 3]
                    byte_val: int = _parse_hex2(hx)
                    result = result + chr(byte_val)
                    i = i + 3
                else:
                    result = result + c
                    i = i + 1
            elif header and c == "_":
                result = result + " "
                i = i + 1
            else:
                result = result + c
                i = i + 1
        if end_nl:
            result = result + "\n"
    return result


def _parse_hex2(s: str) -> int:
    """Parse 2-character uppercase hex string to int."""
    val: int = 0
    for ch in s:
        val = val * 16
        if "0" <= ch <= "9":
            val = val + ord(ch) - ord("0")
        elif "A" <= ch <= "F":
            val = val + ord(ch) - ord("A") + 10
        elif "a" <= ch <= "f":
            val = val + ord(ch) - ord("a") + 10
    return val


def decodestring(s: str, header: int = 0) -> str:
    """Decode quoted-printable encoded string *s*."""
    return decode(s, header)
