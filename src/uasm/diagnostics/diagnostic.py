"""Diagnostics: what the compiler says when something is wrong.

A diagnostic is structured, not a string. It carries a severity, a stable code,
a primary message, any number of labelled spans, and optional notes and help.
That structure is what lets the same object be rendered as text for a terminal,
as JSON for an editor, and be counted, sorted, deduplicated and limited without
parsing prose.

    error[E0104]: cannot add int and str
      --> prog.py:12:11
       |
    12 |     total = count + name
       |             ----- ^ ^^^^ str
       |             |     |
       |             |     this operator
       |             int
       |
       = note: the operands of `+` must have the same type
       = help: convert with str(count) or int(name)

STABLE CODES. Every diagnostic has one, and it never changes meaning. Users
search for them, editors map them to quick-fixes, and test suites assert on
them -- all of which break if a code is reused for a different problem. New
problems get new codes; retired ones stay retired.

SEVERITY IS NOT A LOG LEVEL. `error` means no artifact will be produced.
`warning` means the artifact is produced and is probably not what you meant.
`note` and `help` are never standalone -- they attach to something.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .span import NO_SPAN, Span, is_real


class Severity(enum.IntEnum):
    """Ordered so that `max(severities)` is the run's outcome."""

    HELP = 0
    NOTE = 1
    WARNING = 2
    ERROR = 3
    FATAL = 4          #: stop immediately; the compiler cannot continue

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def is_failure(self) -> bool:
        return self >= Severity.ERROR


_LABELS = {
    Severity.HELP: "help",
    Severity.NOTE: "note",
    Severity.WARNING: "warning",
    Severity.ERROR: "error",
    Severity.FATAL: "fatal",
}


@dataclass(frozen=True, slots=True)
class Label:
    """A span with a message, drawn under the source excerpt.

    Exactly one label per diagnostic is `primary`; it gets the `^^^` marker and
    determines where the `-->` points. Secondary labels get `---` and explain
    context -- the other operand, the earlier definition, the opening bracket.
    """

    span: Span
    message: str = ""
    primary: bool = False


@dataclass(slots=True)
class Diagnostic:
    """One problem, with everything needed to explain it."""

    severity: Severity
    code: str
    message: str
    labels: list[Label] = field(default_factory=list)
    #: Explanations of WHY, shown as `= note:` lines.
    notes: list[str] = field(default_factory=list)
    #: Suggested fixes, shown as `= help:` lines.
    helps: list[str] = field(default_factory=list)

    @property
    def primary_span(self) -> Span:
        for label in self.labels:
            if label.primary:
                return label.span
        return self.labels[0].span if self.labels else NO_SPAN

    @property
    def has_location(self) -> bool:
        return is_real(self.primary_span)

    def sort_key(self) -> tuple:
        """Order by file then position, so output follows the source."""
        span = self.primary_span
        if not is_real(span):
            return ("", 0, 0, self.code)
        return (span.file.name, span.start, span.end, self.code)

    # ── fluent construction ─────────────────────────────────────────────────
    def at(self, span: Span, message: str = "") -> Diagnostic:
        """Add the PRIMARY label. Call once."""
        self.labels.append(Label(span, message, primary=True))
        return self

    def also(self, span: Span, message: str = "") -> Diagnostic:
        """Add a secondary label, for context elsewhere in the source."""
        self.labels.append(Label(span, message, primary=False))
        return self

    def note(self, text: str) -> Diagnostic:
        self.notes.append(text)
        return self

    def help(self, text: str) -> Diagnostic:
        self.helps.append(text)
        return self


def error(code: str, message: str) -> Diagnostic:
    return Diagnostic(Severity.ERROR, code, message)


def warning(code: str, message: str) -> Diagnostic:
    return Diagnostic(Severity.WARNING, code, message)


def fatal(code: str, message: str) -> Diagnostic:
    return Diagnostic(Severity.FATAL, code, message)


class CompilerError(Exception):
    """Raised to abort compilation, carrying the diagnostic that caused it.

    Used only for FATAL problems and for internal invariant failures. Ordinary
    errors are reported to a `DiagnosticSink` and compilation continues, so one
    run reports every problem instead of the first.
    """

    def __init__(self, diagnostic: Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(f"{diagnostic.code}: {diagnostic.message}")
