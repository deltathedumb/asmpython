"""Python frontend: a statically-annotated subset.

    analysis.py   resolve names, assign a type to every expression, report
    lower.py      typed AST -> IR

Lowering runs only if analysis reported nothing, so it contains no validation.

THE SUBSET: annotated int/float/bool parameters and locals, arithmetic and
comparison, if/while/for-over-range, calls, and print. No objects, no dynamic
typing, no exceptions, no closures.

That boundary is honest rather than arbitrary. Everything missing needs a
runtime and an object model, which is a separate body of work; the point of
this frontend is to prove the IR is usable by a real language and to be the
worked example a second frontend is written against.
"""
from __future__ import annotations

import ast

from ...diagnostics import DiagnosticSink, SourceFile, error
from ...frontend import Frontend, register
from ...ir import Module
from .analysis import Analyzer
from .lower import Lowerer


class PythonFrontend(Frontend):
    name = "python"
    extensions = (".py",)
    description = "statically-annotated Python subset"

    def compile(self, source: SourceFile, sink: DiagnosticSink) -> Module | None:
        try:
            tree = ast.parse(source.text, filename=source.name)
        except SyntaxError as exc:
            sink.report(self._syntax_error(source, exc))
            return None

        functions = Analyzer(source, sink).run(tree)
        if sink.failed:
            # Lowering assumes analysis succeeded. Running it anyway would
            # produce IR that fails the verifier, and the user would see an
            # internal-error report on top of the real diagnostics.
            return None
        return Lowerer(functions, source).run()

    @staticmethod
    def _syntax_error(source: SourceFile, exc: SyntaxError):
        line = exc.lineno or 1
        col = max(0, (exc.offset or 1) - 1)
        starts = source.line_starts
        begin = starts[line - 1] + col if line <= len(starts) else 0
        return error("E0000", exc.msg or "invalid syntax").at(
            source.span(begin, begin + 1))


register(PythonFrontend())
