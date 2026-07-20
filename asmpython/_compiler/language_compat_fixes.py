"""Compatibility patches for valid Python forms not yet folded into core passes.

This module is imported by :mod:`asmpython._compiler` before the driver imports
the parser and semantic analyzer. Keep fixes general and covered by native
regressions; once their implementations are moved into the owning pass, remove
the corresponding patch here.
"""

from __future__ import annotations

from . import ast_nodes as A
from .parser import Parser
from .sema import SemaAnalyzer


_ORIGINAL_PARSE_STMT = Parser._parse_stmt
_ORIGINAL_PARSE_TRAILERS = Parser._parse_trailers
_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr


def _parse_stmt_with_yield_from(self: Parser):
    """Lower ``yield from iterable`` through the existing for/yield machinery."""
    token = self._peek()
    next_token = self._peek(1)
    if (
        token.kind == "KEYWORD"
        and token.value == "yield"
        and next_token.kind == "KEYWORD"
        and next_token.value == "from"
    ):
        pos = self._eat().pos
        self._eat()  # from
        iterable = self._parse_expr()
        self._expect("NEWLINE")
        counter = getattr(self, "_yield_from_counter", 0) + 1
        self._yield_from_counter = counter
        item_name = f"__yield_from_{counter}"
        return A.For(
            var=item_name,
            range_args=[],
            iter=iterable,
            body=[
                A.YieldStmt(
                    value=A.Name(name=item_name, pos=pos),
                    pos=pos,
                )
            ],
            pos=pos,
        )
    return _ORIGINAL_PARSE_STMT(self)


def _parse_trailers_with_expression_calls(self: Parser, atom):
    """Parse calls on any primary result, reusing callable-instance dispatch."""
    while True:
        atom = _ORIGINAL_PARSE_TRAILERS(self, atom)
        if not self._check("OP", "("):
            return atom
        lpar = self._eat()
        args, kwargs = self._parse_call_args()
        self._expect("OP", ")")
        atom = A.MethodCall(
            obj=atom,
            method="__call__",
            args=args,
            kwargs=kwargs,
            pos=lpar.pos,
        )


def _check_expr_with_type_constructor(self: SemaAnalyzer, expr, scope) -> None:
    """Resolve ``type(name)(...)`` to the statically known user constructor.

    Restrict the source to a plain name so the lowering never removes evaluation
    side effects from an arbitrary expression.
    """
    if (
        isinstance(expr, A.MethodCall)
        and expr.method == "__call__"
        and isinstance(expr.obj, A.Call)
        and expr.obj.func == "type"
        and len(expr.obj.args) == 1
        and isinstance(expr.obj.args[0], A.Name)
    ):
        source = expr.obj.args[0]
        _ORIGINAL_CHECK_EXPR(self, source, scope)
        source_type = A.expr_type(source)
        if source_type.startswith("instance:"):
            class_name = source_type.split(":", 1)[1]
            expr.__class__ = A.Call
            expr.func = class_name
            expr.dstar = None
            expr.resolved_overload_symbol = None
            self._check_call(expr, scope)
            return
    _ORIGINAL_CHECK_EXPR(self, expr, scope)


if not getattr(Parser, "_asmpython_yield_from_patch", False):
    Parser._parse_stmt = _parse_stmt_with_yield_from
    Parser._asmpython_yield_from_patch = True

if not getattr(Parser, "_asmpython_expression_call_patch", False):
    Parser._parse_trailers = _parse_trailers_with_expression_calls
    Parser._asmpython_expression_call_patch = True

if not getattr(SemaAnalyzer, "_asmpython_type_constructor_patch", False):
    SemaAnalyzer._check_expr = _check_expr_with_type_constructor
    SemaAnalyzer._asmpython_type_constructor_patch = True
