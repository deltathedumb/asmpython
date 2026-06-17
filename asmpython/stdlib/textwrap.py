"""textwrap module: text wrapping and filling.

Pure-Python implementation for wrapping paragraphs of text to a given width.
"""
from __future__ import annotations


def wrap(text: str, width: int = 70) -> list[str]:
    """Wrap text to at most width characters per line. Returns list of lines."""
    if width <= 0:
        width = 70
    words: list = _split_words(text)
    lines: list = []
    current: str = ""
    i: int = 0
    while i < len(words):
        word: str = words[i]
        if len(current) == 0:
            if len(word) <= width:
                current = word
            else:
                start: int = 0
                while start < len(word):
                    lines.append(word[start:start + width])
                    start = start + width
        elif len(current) + 1 + len(word) <= width:
            current = current + " " + word
        else:
            lines.append(current)
            if len(word) <= width:
                current = word
            else:
                start2: int = 0
                while start2 < len(word):
                    chunk: str = word[start2:start2 + width]
                    if start2 + width < len(word):
                        lines.append(chunk)
                    else:
                        current = chunk
                    start2 = start2 + width
        i = i + 1
    if len(current) > 0:
        lines.append(current)
    return lines


def fill(text: str, width: int = 70) -> str:
    """Wrap text and join with newlines."""
    wrapped: list = wrap(text, width)
    result: str = ""
    i: int = 0
    while i < len(wrapped):
        if i > 0:
            result = result + "\n"
        result = result + wrapped[i]
        i = i + 1
    return result


def shorten(text: str, width: int, placeholder: str = " [...]") -> str:
    """Collapse whitespace and shorten text to at most width characters."""
    collapsed: str = _collapse_whitespace(text)
    if len(collapsed) <= width:
        return collapsed
    max_len: int = width - len(placeholder)
    if max_len <= 0:
        return placeholder[:width]
    last_space: int = -1
    i: int = 0
    while i <= max_len and i < len(collapsed):
        if collapsed[i] == " ":
            last_space = i
        i = i + 1
    if last_space > 0:
        return collapsed[:last_space] + placeholder
    return collapsed[:max_len] + placeholder


def dedent(text: str) -> str:
    """Remove common leading whitespace from all lines."""
    lines: list = _split_lines(text)
    if len(lines) == 0:
        return text
    min_indent: int = -1
    i: int = 0
    while i < len(lines):
        line: str = lines[i]
        if len(line) == 0:
            i = i + 1
            continue
        indent: int = 0
        j: int = 0
        while j < len(line) and (line[j] == " " or line[j] == "\t"):
            indent = indent + 1
            j = j + 1
        if min_indent == -1 or indent < min_indent:
            min_indent = indent
        i = i + 1
    if min_indent <= 0:
        return text
    result: str = ""
    i = 0
    while i < len(lines):
        if i > 0:
            result = result + "\n"
        line2: str = lines[i]
        if len(line2) >= min_indent:
            result = result + line2[min_indent:]
        else:
            result = result + line2
        i = i + 1
    return result


def indent(text: str, prefix: str, predicate: int = 0) -> str:
    """Add prefix to beginning of each line in text."""
    lines: list = _split_lines(text)
    result: str = ""
    i: int = 0
    while i < len(lines):
        if i > 0:
            result = result + "\n"
        if len(lines[i]) > 0:
            result = result + prefix + lines[i]
        else:
            result = result + lines[i]
        i = i + 1
    return result


def _split_words(text: str) -> list[str]:
    """Split text on whitespace, returning non-empty words."""
    words: list = []
    current: str = ""
    i: int = 0
    while i < len(text):
        c: str = text[i]
        if c == " " or c == "\t" or c == "\n" or c == "\r":
            if len(current) > 0:
                words.append(current)
                current = ""
        else:
            current = current + c
        i = i + 1
    if len(current) > 0:
        words.append(current)
    return words


def _split_lines(text: str) -> list[str]:
    """Split text into lines (on \\n)."""
    lines: list = []
    current: str = ""
    i: int = 0
    while i < len(text):
        c: str = text[i]
        if c == "\n":
            lines.append(current)
            current = ""
        else:
            current = current + c
        i = i + 1
    lines.append(current)
    return lines


def _collapse_whitespace(text: str) -> str:
    """Replace runs of whitespace with a single space and strip."""
    result: str = ""
    in_space: int = 0
    i: int = 0
    while i < len(text):
        c: str = text[i]
        if c == " " or c == "\t" or c == "\n" or c == "\r":
            if in_space == 0 and len(result) > 0:
                result = result + " "
            in_space = 1
        else:
            result = result + c
            in_space = 0
        i = i + 1
    return result


class TextWrapper:
    """Object with wrap/fill methods for reusable configuration."""

    def __init__(self, width: int = 70, initial_indent: str = "",
                 subsequent_indent: str = "") -> None:
        self.width: int = width
        self.initial_indent: str = initial_indent
        self.subsequent_indent: str = subsequent_indent

    def wrap(self, text: str) -> list[str]:
        raw: list[str] = wrap(text, self.width - len(self.initial_indent))
        result: list[str] = []
        i: int = 0
        while i < len(raw):
            if i == 0:
                result.append(self.initial_indent + raw[i])
            else:
                result.append(self.subsequent_indent + raw[i])
            i = i + 1
        return result

    def fill(self, text: str) -> str:
        wrapped: list = self.wrap(text)
        result: str = ""
        i: int = 0
        while i < len(wrapped):
            if i > 0:
                result = result + "\n"
            result = result + wrapped[i]
            i = i + 1
        return result
