"""csv module: CSV reading and writing (RFC 4180-ish, default dialect only).

Implements:
  - reader()     — parse a list of lines into a list[list[str]] of rows.
  - writer_row() — format one row (list[str]) as a single CSV line (no
                    trailing newline).
  - writer_rows()— format a list[list[str]] as a list of CSV lines.
  - DictReader   — parses a header row + data rows; `.fieldnames: list[str]`,
                    `.rows: list[list[str]]`, plus `.get(row, name)` for
                    named field access.

Limitations vs CPython:
  - Only the default dialect: comma delimiter, double-quote quoting, doubled
    quotes (`""`) escape a literal quote inside a quoted field.
  - No support for a quoted field whose value spans multiple input lines
    (embedded newlines) — each element of `lines` is one record.
  - reader()/writer_row() operate on in-memory list[str], not file objects —
    asmpython has no file-iterator protocol to drive a lazy csv.reader(f).
  - No DictWriter.
  - writer_row() does not append a line terminator; callers add \"\\n\" (or
    \"\\r\\n\" for CPython-identical output) themselves.
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


def reader(lines: list[str]) -> list[list[str]]:
    """Parse `lines` (one CSV record per element) into a list of rows.

    Each row is a list[str] of field values. Compatible with CPython's
    csv.reader() which also yields lists of strings.
    """
    result: list[list[str]] = []
    for line in lines:
        result.append(_parse_line(line))
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


def writer_rows(rows: list[list[str]]) -> list[str]:
    """Format each row (list[str]) as a CSV line (no terminators)."""
    result: list[str] = []
    for row in rows:
        result.append(writer_row(row))
    return result


QUOTE_MINIMAL: int = 0
QUOTE_ALL: int = 1
QUOTE_NONNUMERIC: int = 2
QUOTE_NONE: int = 3


_dialects: list = []
_dialect_names: list = []


class Dialect:
    """CSV dialect descriptor."""

    def __init__(self, name: str = "excel", delimiter: str = ",",
                 quotechar: str = '"', quoting: int = 0,
                 lineterminator: str = "\r\n", doublequote: int = 1,
                 skipinitialspace: int = 0, strict: int = 0) -> None:
        self.name: str = name
        self.delimiter: str = delimiter
        self.quotechar: str = quotechar
        self.quoting: int = quoting
        self.lineterminator: str = lineterminator
        self.doublequote: int = doublequote
        self.skipinitialspace: int = skipinitialspace
        self.strict: int = strict


def register_dialect(name: str, dialect: Dialect) -> None:
    """Register a Dialect under name."""
    i: int = 0
    while i < len(_dialect_names):
        if _dialect_names[i] == name:
            _dialects[i] = dialect
            return
        i = i + 1
    _dialect_names.append(name)
    _dialects.append(dialect)


def get_dialect(name: str) -> Dialect:
    """Return the Dialect registered under name."""
    i: int = 0
    while i < len(_dialect_names):
        if _dialect_names[i] == name:
            return _dialects[i]
        i = i + 1
    return Dialect(name)


def list_dialects() -> list:
    """Return list of registered dialect names."""
    result: list[str] = []
    for n in _dialect_names:
        result.append(n)
    return result


class DictReader:
    """Parse `lines` using the first row as field names.

    `.fieldnames` holds the header row; `.rows` holds the remaining data
    rows as `list[str]` (positional, in `fieldnames` order). Use `.get(row,
    name)` for named field access.
    """

    def __init__(self, lines: list[str]) -> None:
        parsed = reader(lines)
        self.fieldnames: list[str] = []
        self.rows: list[list[str]] = []
        if len(parsed) == 0:
            return
        self.fieldnames = parsed[0]
        i: int = 1
        while i < len(parsed):
            self.rows.append(parsed[i])
            i = i + 1

    def get(self, row: list[str], name: str) -> str:
        i: int = 0
        while i < len(self.fieldnames):
            if self.fieldnames[i] == name:
                if i < len(row):
                    return row[i]
                return ""
            i = i + 1
        raise KeyError(name)
