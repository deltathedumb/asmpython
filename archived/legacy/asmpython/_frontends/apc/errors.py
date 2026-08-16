"""Diagnostics for the APC frontend.

Every error carries a source position so the message can point at the line the
programmer wrote, rather than at an IR instruction they never typed.
"""

from __future__ import annotations


class APCError(Exception):
    """A lex/parse/emit error with a source position."""

    def __init__(self, message: str, line: int = 0, col: int = 0, src: str = "") -> None:
        self.message = message
        self.line = line
        self.col = col
        self.src = src
        super().__init__(self.render())

    def render(self) -> str:
        head = f"apc:{self.line}:{self.col}: {self.message}"
        if not self.src or self.line <= 0:
            return head
        lines = self.src.splitlines()
        if self.line > len(lines):
            return head
        text = lines[self.line - 1]
        caret = " " * max(0, self.col - 1) + "^"
        return f"{head}\n    {text}\n    {caret}"


__all__ = ["APCError"]
