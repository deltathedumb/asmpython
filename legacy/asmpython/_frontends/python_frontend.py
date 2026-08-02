"""The built-in Python frontend: ASMPython's default source language.

This is the real implementation behind ``--frontend python`` (the default). It
is a thin adapter over the existing lexer/parser/sema, producing exactly the
typed module the IR pipeline has always consumed -- so routing the driver
through the frontend registry is behavior-preserving for ordinary builds.
"""

from __future__ import annotations

from .._compiler.ssa.ir import FrontendContext, IRFrontend
from .._compiler.lexer import Lexer
from .._compiler.parser import Parser
from .._compiler.program import load_program
from .._compiler.sema import analyze as sema_analyze


class PythonFrontend(IRFrontend):
    """Lex/parse/sema the ASMPython Python subset into a typed module."""

    name = "python"
    source_extensions = (".py",)
    production_suitable = True

    def parse(self, src: str, ctx: FrontendContext) -> object:
        if ctx.whole_program and ctx.entry_path is not None:
            module = load_program(
                src, ctx.entry_path, active_extensions=ctx.active_extensions
            )
        else:
            tokens = Lexer(src).tokenize()
            module = Parser(tokens, ctx.active_extensions).parse()
        sema_analyze(
            module,
            source_dir=ctx.source_dir,
            collect_errors=ctx.all_errors,
            active_extensions=ctx.active_extensions,
        )
        return module


__all__ = ["PythonFrontend"]
