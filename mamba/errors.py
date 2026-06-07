"""Diagnostics infrastructure shared by every compiler phase.

Each phase raises a `CompileError` carrying the source location it failed at.
The CLI formats these with a caret-pointer so the user sees exactly where the
problem is, instead of a bare exception trace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class SourcePos:
    line: int
    col: int


class CompileError(Exception):
    """Base class for any user-facing compile-time failure.

    Carries a SourcePos so the driver can render a pointer into the source.
    Phase tag distinguishes 'lex error' vs 'parse error' vs 'name error' etc.
    """

    def __init__(self, phase: str, message: str, pos: Optional[SourcePos] = None) -> None:
        super().__init__(message)
        self.phase = phase
        self.message = message
        self.pos = pos

    def format(self, source: str, filename: str = "<input>") -> str:
        out: list[str] = []
        if self.pos is not None:
            out.append(f"{filename}:{self.pos.line}:{self.pos.col}: {self.phase} error: {self.message}")
            line_text = _get_line(source, self.pos.line)
            if line_text is not None:
                out.append("  " + line_text.rstrip("\n"))
                out.append("  " + " " * (self.pos.col - 1) + "^")
        else:
            out.append(f"{filename}: {self.phase} error: {self.message}")
        return "\n".join(out)


class LexError(CompileError):
    def __init__(self, message: str, pos: Optional[SourcePos] = None) -> None:
        super().__init__("lex", message, pos)


class ParseError(CompileError):
    def __init__(self, message: str, pos: Optional[SourcePos] = None) -> None:
        super().__init__("parse", message, pos)


class SemaError(CompileError):
    def __init__(self, message: str, pos: Optional[SourcePos] = None) -> None:
        super().__init__("semantic", message, pos)


def _get_line(source: str, lineno: int) -> Optional[str]:
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1]
    return None
