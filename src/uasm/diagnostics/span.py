"""Source positions.

Every diagnostic points at source, and to point at source you need three things
the compiler must carry end to end: which file, which byte range, and the text
itself so a caret can be drawn under it.

The unit is a BYTE OFFSET into the file, not a (line, column) pair. Offsets are
cheap to produce, cheap to store on an AST node, and unambiguous; line and
column are derived only when a diagnostic is actually rendered, which is rare.
Storing line/column instead means every node pays for information almost none
of them ever use, and means tabs and multi-byte characters have to be resolved
at the wrong moment.

`SourceFile` owns the line index. It is built once, lazily, and binary-searched,
so rendering a diagnostic in a 100k-line file costs a log-time lookup rather
than a scan.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Location:
    """A resolved position, 1-based, for display only."""

    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.line}:{self.column}"


class SourceFile:
    """One unit of source text, with a lazily-built line index."""

    __slots__ = ("path", "text", "_line_starts", "_name")

    def __init__(self, text: str, path: Path | str | None = None) -> None:
        self.text = text
        self.path = Path(path) if path is not None else None
        self._name = str(path) if path is not None else "<source>"
        self._line_starts: list[int] | None = None

    @classmethod
    def read(cls, path: Path | str) -> SourceFile:
        p = Path(path)
        return cls(p.read_text(encoding="utf-8"), p)

    @property
    def name(self) -> str:
        return self._name

    @property
    def line_starts(self) -> list[int]:
        if self._line_starts is None:
            starts = [0]
            for i, ch in enumerate(self.text):
                if ch == "\n":
                    starts.append(i + 1)
            self._line_starts = starts
        return self._line_starts

    def location(self, offset: int) -> Location:
        """Resolve a byte offset to a 1-based line and column."""
        offset = max(0, min(offset, len(self.text)))
        starts = self.line_starts
        idx = bisect.bisect_right(starts, offset) - 1
        return Location(idx + 1, offset - starts[idx] + 1)

    def line_text(self, line: int) -> str:
        """The text of a 1-based line, without its newline."""
        starts = self.line_starts
        if not 1 <= line <= len(starts):
            return ""
        begin = starts[line - 1]
        end = starts[line] - 1 if line < len(starts) else len(self.text)
        return self.text[begin:end].rstrip("\r")

    @property
    def line_count(self) -> int:
        return len(self.line_starts)

    def span(self, start: int, end: int | None = None) -> Span:
        return Span(self, start, end if end is not None else start + 1)

    def __repr__(self) -> str:
        return f"<SourceFile {self._name} ({len(self.text)} chars)>"


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open byte range `[start, end)` within one file.

    Half-open so that an empty span (start == end) is representable, which is
    what you want for "expected something here" diagnostics that point at a
    position rather than at existing text.
    """

    file: SourceFile
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"span end {self.end} precedes start {self.start}")

    @property
    def start_loc(self) -> Location:
        return self.file.location(self.start)

    @property
    def end_loc(self) -> Location:
        return self.file.location(max(self.start, self.end - 1))

    @property
    def text(self) -> str:
        return self.file.text[self.start:self.end]

    @property
    def is_multiline(self) -> bool:
        return self.start_loc.line != self.end_loc.line

    def to(self, other: Span) -> Span:
        """The span covering both. Used to widen a node's span to its children."""
        if other.file is not self.file:
            raise ValueError("cannot join spans from different files")
        return Span(self.file, min(self.start, other.start),
                    max(self.end, other.end))

    def __str__(self) -> str:
        return f"{self.file.name}:{self.start_loc}"


#: A span that points nowhere, for diagnostics that genuinely have no position
#: (a bad command-line flag, a missing entry point). Rendering skips the source
#: excerpt for these rather than inventing one.
_EMPTY_FILE = SourceFile("", None)
NO_SPAN = Span(_EMPTY_FILE, 0, 0)


def is_real(span: Span) -> bool:
    return span.file is not _EMPTY_FILE
