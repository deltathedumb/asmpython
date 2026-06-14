"""csv module: CSV reading and writing (RFC 4180-ish, default dialect only).

Implements:
  - Row          — one parsed row; `.fields: list[str]`, `__getitem__`,
                    `__len__`, `__repr__`.
  - reader()     — parse a list of lines into a list of `Row`.
  - writer_row() — format one row (`list[str]`) as a single CSV line (no
                    trailing newline).
  - writer_rows()— format a `list[Row]` as a list of CSV lines.
  - DictReader   — parses a header row + data rows; `.fieldnames: list[str]`,
                    `.rows: list[Row]`, plus `.get(row, name)` for named
                    field access.

Limitations vs CPython:
  - Only the default dialect: comma delimiter, double-quote quoting, doubled
    quotes (`""`) escape a literal quote inside a quoted field.
  - No support for a quoted field whose value spans multiple input lines
    (embedded newlines) — each element of `lines` is one record.
  - reader()/writer_row() operate on in-memory `list[str]`, not file
    objects — asmpython has no file-iterator protocol to drive a lazy
    `csv.reader(f)`.
  - No `DictWriter` / `dict`-based rows: asmpython's flat list-element-type
    system can't express `list[dict[str, str]]`, so rows stay `Row`
    (`list[str]`) and `DictReader` exposes named access via `.get()` instead.
  - writer_row() does not append a line terminator; callers add `"\n"` (or
    `"\r\n"` for CPython-identical output) themselves.
"""
from __future__ import annotations


class Row:
    """One parsed CSV row: a fixed sequence of string fields."""

    def __init__(self, fields: list[str]) -> None:
        self.fields: list[str] = fields

    def __getitem__(self, i: int) -> str:
        return self.fields[i]

    def __len__(self) -> int:
        return len(self.fields)

    def __repr__(self) -> str:
        return repr(self.fields)


def _parse_line(line: str) -> list[str]:
    n = len(line)
    while n > 0 and (line[n - 1] == "\n" or line[n - 1] == "\r"):
        n = n - 1
    if n == 0:
        return []
    fields: list[str] = []
    i = 0
    while i <= n:
        if i < n and line[i] == '"':
            i = i + 1
            field = ""
            while i < n:
                c = line[i]
                if c == '"':
                    if i + 1 < n and line[i + 1] == '"':
                        field = field + '"'
                        i = i + 2
                    else:
                        i = i + 1
                        break
                else:
                    field = field + c
                    i = i + 1
            while i < n and line[i] != ",":
                i = i + 1
            fields.append(field)
            i = i + 1
        else:
            start = i
            while i < n and line[i] != ",":
                i = i + 1
            fields.append(line[start:i])
            i = i + 1
    return fields


def reader(lines: list[str]) -> list[Row]:
    """Parse `lines` (one CSV record per element) into a list of `Row`."""
    result: list[Row] = []
    for line in lines:
        result.append(Row(_parse_line(line)))
    return result


def _quote_field(field: str) -> str:
    needs_quote = False
    i = 0
    n = len(field)
    while i < n:
        c = field[i]
        if c == "," or c == '"' or c == "\n" or c == "\r":
            needs_quote = True
            break
        i = i + 1
    if not needs_quote:
        return field
    out = '"'
    i = 0
    while i < n:
        c = field[i]
        if c == '"':
            out = out + '""'
        else:
            out = out + c
        i = i + 1
    out = out + '"'
    return out


def writer_row(fields: list[str]) -> str:
    """Format `fields` as one CSV line, quoting as needed (no terminator)."""
    parts: list[str] = []
    for f in fields:
        parts.append(_quote_field(f))
    return ",".join(parts)


def writer_rows(rows: list[Row]) -> list[str]:
    """Format each `Row` in `rows` as a CSV line (no terminators)."""
    result: list[str] = []
    for row in rows:
        result.append(writer_row(row.fields))
    return result


class DictReader:
    """Parse `lines` using the first row as field names.

    `.fieldnames` holds the header row; `.rows` holds the remaining data
    rows as `Row` (positional, in `fieldnames` order). Use `.get(row, name)`
    for named access.
    """

    def __init__(self, lines: list[str]) -> None:
        parsed = reader(lines)
        self.fieldnames: list[str] = []
        self.rows: list[Row] = []
        if len(parsed) == 0:
            return
        self.fieldnames = parsed[0].fields
        i = 1
        while i < len(parsed):
            self.rows.append(parsed[i])
            i = i + 1

    def get(self, row: Row, name: str) -> str:
        i = 0
        while i < len(self.fieldnames):
            if self.fieldnames[i] == name:
                if i < len(row.fields):
                    return row.fields[i]
                return ""
            i = i + 1
        raise KeyError(name)
