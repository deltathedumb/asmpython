"""Rendering diagnostics for a terminal.

The output format is the one rustc popularised, because it has been tested on
more confused people than any alternative:

    error[E0104]: cannot add int and str
      --> prog.py:3:13
       |
     3 |     total = count + name
       |             ----- ^ ^^^^ str
       |             |     |
       |             |     this operator
       |             int
       |
       = help: convert with str(count)

Three properties are worth the code they cost.

MULTIPLE LABELS ON ONE LINE, drawn at their real columns, with messages stacked
underneath in reverse order so the connecting bars never cross. A diagnostic
about a binary operator wants to point at three things at once; rendering them
as three separate diagnostics loses the relationship between them.

THE GUTTER WIDTH FOLLOWS THE LINE NUMBER, so a 5-digit line number does not
misalign the excerpt.

TABS ARE EXPANDED before the caret row is computed. Otherwise every caret in a
tab-indented file points at the wrong column -- and tab-indented files are
exactly the ones where the author is least likely to suspect the compiler.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from .diagnostic import Diagnostic, Label, Severity
from .span import Span, is_real

TAB_WIDTH = 4


@dataclass(frozen=True, slots=True)
class Style:
    """ANSI colours, or a no-op set when the stream is not a terminal."""

    reset: str = ""
    bold: str = ""
    error: str = ""
    warning: str = ""
    note: str = ""
    help: str = ""
    gutter: str = ""

    @staticmethod
    def coloured() -> Style:
        return Style(
            reset="\033[0m", bold="\033[1m", error="\033[1;31m",
            warning="\033[1;33m", note="\033[1;36m", help="\033[1;32m",
            gutter="\033[1;34m",
        )

    @staticmethod
    def plain() -> Style:
        return Style()

    def for_severity(self, sev: Severity) -> str:
        return {
            Severity.FATAL: self.error, Severity.ERROR: self.error,
            Severity.WARNING: self.warning, Severity.NOTE: self.note,
            Severity.HELP: self.help,
        }.get(sev, "")


def supports_colour(stream) -> bool:
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    import os
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return True


class Renderer:
    """Turns diagnostics into terminal text."""

    def __init__(self, style: Style | None = None, *, context: int = 0) -> None:
        self.style = style or Style.plain()
        self.context = context

    @classmethod
    def for_stream(cls, stream=None, **kw) -> Renderer:
        stream = stream or sys.stderr
        return cls(Style.coloured() if supports_colour(stream) else Style.plain(),
                   **kw)

    def render(self, d: Diagnostic) -> str:
        s = self.style
        colour = s.for_severity(d.severity)
        code = f"[{d.code}]" if d.code else ""
        out = [f"{colour}{d.severity.label}{code}{s.reset}"
               f"{s.bold}: {d.message}{s.reset}"]

        if not d.has_location:
            for n in d.notes:
                out.append(f"  = {s.note}note{s.reset}: {n}")
            for h in d.helps:
                out.append(f"  = {s.help}help{s.reset}: {h}")
            return "\n".join(out)

        primary = d.primary_span
        by_line = _group_by_line(d.labels)
        width = max(len(str(line)) for line in by_line) if by_line else 1
        pad = " " * width
        bar = f"{s.gutter}|{s.reset}"

        loc = primary.start_loc
        out.append(f"{pad}{s.gutter}-->{s.reset} "
                   f"{primary.file.name}:{loc.line}:{loc.column}")
        out.append(f"{pad} {bar}")

        prev_line: int | None = None
        for line_no in sorted(by_line):
            if prev_line is not None and line_no > prev_line + 1:
                out.append(f"{s.gutter}...{s.reset}")
            prev_line = line_no
            labels = by_line[line_no]
            source = _expand_tabs(primary.file.line_text(line_no))
            out.append(f"{s.gutter}{line_no:>{width}}{s.reset} {bar} {source}")
            out.extend(f"{pad} {bar} {row}"
                       for row in self._marker_rows(labels, line_no, primary.file))

        out.append(f"{pad} {bar}")
        for n in d.notes:
            out.append(f"{pad} = {s.note}note{s.reset}: {n}")
        for h in d.helps:
            out.append(f"{pad} = {s.help}help{s.reset}: {h}")
        return "\n".join(out)

    def _marker_rows(self, labels: list[Label], line_no: int, file) -> list[str]:
        """The caret row, then one row per labelled message.

        Messages are emitted in REVERSE column order so that a label's vertical
        bar never has to cross a message printed to its right.
        """
        s = self.style
        placed = []
        for lab in labels:
            col, width = _columns(lab.span, line_no, file)
            placed.append((col, width, lab))
        placed.sort(key=lambda p: p[0])

        caret = []
        for col, width, lab in placed:
            _pad_to(caret, col)
            mark = "^" if lab.primary else "-"
            colour = s.error if lab.primary else s.note
            caret.append(f"{colour}{mark * max(1, width)}{s.reset}")
        rows = ["".join(caret)]

        with_msg = [(c, w, l) for c, w, l in placed if l.message]
        if not with_msg:
            return rows
        # The rightmost message goes on the caret row itself; the rest stack.
        last_col, last_w, last_lab = with_msg[-1]
        colour = s.error if last_lab.primary else s.note
        rows[0] = rows[0] + f" {colour}{last_lab.message}{s.reset}"

        remaining = with_msg[:-1]
        while remaining:
            row: list[str] = []
            for col, _w, _l in remaining[:-1]:
                _pad_to(row, col)
                row.append(f"{s.gutter}|{s.reset}")
            col, _w, lab = remaining[-1]
            _pad_to(row, col)
            colour = s.error if lab.primary else s.note
            row.append(f"{colour}{lab.message}{s.reset}")
            rows.append("".join(row))
            remaining = remaining[:-1]
        return rows


def _visible_len(parts: list[str]) -> int:
    """Length ignoring ANSI escapes, so padding stays correct in colour."""
    n = 0
    for p in parts:
        i = 0
        while i < len(p):
            if p[i] == "\033":
                i = p.index("m", i) + 1 if "m" in p[i:] else len(p)
            else:
                n += 1
                i += 1
    return n


def _pad_to(parts: list[str], column: int) -> None:
    gap = column - _visible_len(parts)
    if gap > 0:
        parts.append(" " * gap)


def _expand_tabs(text: str) -> str:
    return text.expandtabs(TAB_WIDTH)


def _columns(span: Span, line_no: int, file) -> tuple[int, int]:
    """(0-based start column, width) of `span` on `line_no`, tabs expanded."""
    starts = file.line_starts
    line_begin = starts[line_no - 1]
    line_end = (starts[line_no] - 1 if line_no < len(starts) else len(file.text))
    begin = max(span.start, line_begin)
    end = min(span.end, line_end)
    raw = file.text[line_begin:begin]
    col = len(_expand_tabs(raw))
    width = len(_expand_tabs(file.text[begin:end])) if end > begin else 1
    return col, width


def _group_by_line(labels: list[Label]) -> dict[int, list[Label]]:
    """Labels by the line they start on, dropping positionless ones.

    A multi-line span is anchored at its first line. Drawing a box around a
    twelve-line function body buries the one line that matters; pointing at its
    header does not.
    """
    out: dict[int, list[Label]] = {}
    for lab in labels:
        if not is_real(lab.span):
            continue
        out.setdefault(lab.span.start_loc.line, []).append(lab)
    return out
