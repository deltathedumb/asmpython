"""Collecting diagnostics.

A sink is where every stage reports problems. It exists so that a compilation
reports EVERY error it can find rather than the first, which is the difference
between one edit-compile cycle and eight.

That only works if later stages can keep running after an error, which means
they must not depend on earlier stages having succeeded. The convention here is
poisoning: when a stage cannot produce a real result it produces a marked
"error" value that downstream stages accept silently. A type checker that hits
an unknown name yields the error type, and every subsequent operation on the
error type is silently fine -- so one unknown name produces one diagnostic, not
one per use.

`DiagnosticSink` also enforces the limits that keep output usable: a cap on how
many are reported, and deduplication, because a loop that reports inside it
will otherwise emit the same message a thousand times.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from .diagnostic import CompilerError, Diagnostic, Severity
from .renderer import Renderer


@dataclass
class DiagnosticSink:
    """Collects diagnostics and decides when to give up."""

    #: Stop reporting (but keep compiling) after this many errors.
    max_errors: int = 100
    #: Treat warnings as errors.
    warnings_are_errors: bool = False
    #: Suppress warnings entirely.
    quiet_warnings: bool = False

    diagnostics: list[Diagnostic] = field(default_factory=list)
    _seen: set[tuple] = field(default_factory=set, repr=False)
    _suppressed: int = 0

    # ── reporting ───────────────────────────────────────────────────────────
    def report(self, d: Diagnostic) -> None:
        """Record a diagnostic. Raises CompilerError only for FATAL."""
        if d.severity is Severity.WARNING:
            if self.quiet_warnings:
                return
            if self.warnings_are_errors:
                d.severity = Severity.ERROR

        key = (d.code, d.message, d.sort_key())
        if key in self._seen:
            return
        self._seen.add(key)

        if d.severity.is_failure and self.error_count >= self.max_errors:
            self._suppressed += 1
            return

        self.diagnostics.append(d)
        if d.severity is Severity.FATAL:
            raise CompilerError(d)

    # ── state ───────────────────────────────────────────────────────────────
    @property
    def error_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity.is_failure)

    @property
    def warning_count(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity is Severity.WARNING)

    @property
    def failed(self) -> bool:
        return self.error_count > 0

    def __bool__(self) -> bool:
        raise TypeError(
            "check sink.failed explicitly; `if sink:` reads as 'if there are "
            "diagnostics' but would be true for warnings alone"
        )

    # ── output ──────────────────────────────────────────────────────────────
    def emit(self, stream=None, renderer: Renderer | None = None) -> None:
        """Write every diagnostic, in source order, to `stream`."""
        stream = stream or sys.stderr
        renderer = renderer or Renderer.for_stream(stream)
        for d in sorted(self.diagnostics, key=Diagnostic.sort_key):
            stream.write(renderer.render(d) + "\n\n")
        if self._suppressed:
            stream.write(
                f"({self._suppressed} further error(s) not shown; "
                f"raise the limit with --max-errors)\n"
            )
        stream.write(self.summary() + "\n")

    def summary(self) -> str:
        parts = []
        if self.error_count:
            n = self.error_count
            parts.append(f"{n} error{'s' if n != 1 else ''}")
        if self.warning_count:
            n = self.warning_count
            parts.append(f"{n} warning{'s' if n != 1 else ''}")
        return "; ".join(parts) + " emitted" if parts else "no diagnostics"

    def clear(self) -> None:
        self.diagnostics.clear()
        self._seen.clear()
        self._suppressed = 0
