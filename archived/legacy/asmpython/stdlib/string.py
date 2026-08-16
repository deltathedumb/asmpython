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


class Formatter:
    """String formatting class compatible with CPython's string.Formatter.

    Simplified: only supports positional {0}, {1} and empty {} references.
    """

    def format(self, format_string: str, a0: str = "", a1: str = "",
               a2: str = "", a3: str = "") -> str:
        """Format format_string replacing {} and {0}..{3} with args."""
        args: list[str] = [a0, a1, a2, a3]
        result: str = ""
        i: int = 0
        n: int = len(format_string)
        auto_idx: int = 0
        while i < n:
            c: str = format_string[i]
            if c == "{":
                if i + 1 < n and format_string[i + 1] == "{":
                    result = result + "{"
                    i = i + 2
                else:
                    j: int = i + 1
                    while j < n and format_string[j] != "}":
                        j = j + 1
                    key: str = format_string[i + 1:j]
                    if key == "" or key == "0":
                        result = result + args[0]
                    elif key == "1":
                        result = result + args[1]
                    elif key == "2":
                        result = result + args[2]
                    elif key == "3":
                        result = result + args[3]
                    else:
                        result = result + "{" + key + "}"
                    i = j + 1
            elif c == "}":
                if i + 1 < n and format_string[i + 1] == "}":
                    result = result + "}"
                    i = i + 2
                else:
                    i = i + 1
            else:
                result = result + c
                i = i + 1
        return result

    def vformat(self, format_string: str, args: list) -> str:
        """Format using a list of args."""
        a0: str = args[0] if len(args) > 0 else ""
        a1: str = args[1] if len(args) > 1 else ""
        a2: str = args[2] if len(args) > 2 else ""
        a3: str = args[3] if len(args) > 3 else ""
        return self.format(format_string, a0, a1, a2, a3)

    def parse(self, format_string: str) -> list:
        """Parse format string; returns list of (literal, field_name) pairs."""
        result: list = []
        i: int = 0
        n: int = len(format_string)
        lit: str = ""
        while i < n:
            c: str = format_string[i]
            if c == "{":
                j: int = i + 1
                while j < n and format_string[j] != "}":
                    j = j + 1
                key: str = format_string[i + 1:j]
                pair: list[str] = [lit, key]
                result.append(pair)
                lit = ""
                i = j + 1
            else:
                lit = lit + c
                i = i + 1
        if len(lit) > 0:
            pair2: list[str] = [lit, ""]
            result.append(pair2)
        return result


class Template:
    """Simple $-substitution template (PEP 292-style).

    Supports $identifier and ${identifier}. $$ is a literal $.
    """

    def __init__(self, template: str) -> None:
        self.template: str = template

    def substitute(self, mapping: int = 0, var0: str = "",
                   key0: str = "") -> str:
        """Substitute $-placeholders using key0=var0 pairs (simplified)."""
        result: str = ""
        i: int = 0
        n: int = len(self.template)
        while i < n:
            c: str = self.template[i]
            if c == "$":
                if i + 1 < n and self.template[i + 1] == "$":
                    result = result + "$"
                    i = i + 2
                elif i + 1 < n and self.template[i + 1] == "{":
                    j: int = i + 2
                    while j < n and self.template[j] != "}":
                        j = j + 1
                    name: str = self.template[i + 2:j]
                    if name == key0:
                        result = result + var0
                    else:
                        result = result + "${" + name + "}"
                    i = j + 1
                else:
                    j2: int = i + 1
                    while j2 < n and (self.template[j2].isalpha() or self.template[j2] == "_"):
                        j2 = j2 + 1
                    name2: str = self.template[i + 1:j2]
                    if name2 == key0:
                        result = result + var0
                    else:
                        result = result + "$" + name2
                    i = j2
            else:
                result = result + c
                i = i + 1
        return result

    def safe_substitute(self, mapping: int = 0, var0: str = "",
                        key0: str = "") -> str:
        """Like substitute but leaves unrecognized placeholders intact."""
        return self.substitute(mapping, var0, key0)
