"""Structured diagnostics: spans, severities, rendering, collection.

    from apc.diagnostics import error, SourceFile, DiagnosticSink

    src  = SourceFile.read("prog.py")
    sink = DiagnosticSink()
    sink.report(
        error("E0104", "cannot add int and str")
            .at(src.span(20, 24), "int")
            .also(src.span(27, 31), "str")
            .help("convert with str(count)")
    )
    sink.emit()

Errors are REPORTED, not raised. A stage keeps going after one so that a single
run finds every problem; only `fatal` aborts. See sink.py on poisoning, which
is what makes continuing safe.
"""
from .diagnostic import (
    CompilerError, Diagnostic, Label, Severity, error, fatal, warning,
)
from .renderer import Renderer, Style
from .sink import DiagnosticSink
from .span import NO_SPAN, Location, SourceFile, Span, is_real

__all__ = [
    "CompilerError", "Diagnostic", "DiagnosticSink", "Label", "Location",
    "NO_SPAN", "Renderer", "Severity", "SourceFile", "Span", "Style",
    "error", "fatal", "is_real", "warning",
]
