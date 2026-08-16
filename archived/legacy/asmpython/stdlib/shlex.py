"""shlex module: shell-like lexer and token splitter."""
from __future__ import annotations


def split(s: str, comments: int = 0, posix: int = 1) -> list[str]:
    """Split a shell-like string into a list of tokens.

    Handles single quotes, double quotes, and backslash escapes.
    """
    tokens: list[str] = []
    token: str = ""
    in_single: int = 0
    in_double: int = 0
    i: int = 0
    n: int = len(s)
    while i < n:
        c: str = s[i]
        if c == "#" and comments and not in_single and not in_double:
            break
        elif in_single:
            if c == "'":
                in_single = 0
            else:
                token = token + c
        elif in_double:
            if c == '"':
                in_double = 0
            elif c == "\\" and posix and i + 1 < n:
                i = i + 1
                nc: str = s[i]
                if nc == '"' or nc == "\\" or nc == "$" or nc == "`" or nc == "\n":
                    token = token + nc
                else:
                    token = token + "\\" + nc
            else:
                token = token + c
        elif c == "'":
            in_single = 1
        elif c == '"':
            in_double = 1
        elif c == "\\" and posix and i + 1 < n:
            i = i + 1
            token = token + s[i]
        elif c == " " or c == "\t" or c == "\n" or c == "\r":
            if token != "" or in_single or in_double:
                tokens.append(token)
                token = ""
        else:
            token = token + c
        i = i + 1
    if token != "":
        tokens.append(token)
    if in_single:
        raise ValueError("No closing quotation")
    if in_double:
        raise ValueError("No closing quotation")
    return tokens


def quote(s: str) -> str:
    """Return a shell-escaped version of the string s."""
    if len(s) == 0:
        return "''"
    safe: int = 1
    i: int = 0
    n: int = len(s)
    while i < n:
        c: str = s[i]
        if not ((c >= "a" and c <= "z") or (c >= "A" and c <= "Z") or
                (c >= "0" and c <= "9") or c == "_" or c == "-" or
                c == "." or c == "/" or c == ":" or c == "@"):
            safe = 0
            break
        i = i + 1
    if safe:
        return s
    result: str = "'"
    i = 0
    while i < n:
        c = s[i]
        if c == "'":
            result = result + "'\\''"
        else:
            result = result + c
        i = i + 1
    result = result + "'"
    return result


def join(tokens: list[str]) -> str:
    """Concatenate tokens with spaces, quoting as needed."""
    parts: list[str] = []
    for t in tokens:
        parts.append(quote(t))
    return " ".join(parts)


class shlex:
    """A simple shlex tokenizer class."""

    def __init__(self, s: str = "", posix: int = 1) -> None:
        self._s: str = s
        self._i: int = 0
        self.posix: int = posix
        self.whitespace: str = " \t\n\r"
        self.wordchars: str = "abcdfeghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
        self.quotes: str = "'\""
        self.commenters: str = "#"

    def get_token(self) -> str:
        n: int = len(self._s)
        while self._i < n and self._s[self._i] in self.whitespace:
            self._i = self._i + 1
        if self._i >= n:
            return ""
        c: str = self._s[self._i]
        if c in self.commenters:
            return ""
        token: str = ""
        while self._i < n:
            c = self._s[self._i]
            if c in self.whitespace:
                break
            token = token + c
            self._i = self._i + 1
        return token

    def __iter__(self) -> list[str]:
        return split(self._s)
