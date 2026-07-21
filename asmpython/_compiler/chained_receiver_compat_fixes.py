"""Type non-trivial method receivers before resolving the outer call.

Descriptor lowering can turn ``obj.property.method()`` into nested method-call
nodes. The outer call historically resolved its receiver from the parser's
integer fallback before the inner property getter had been semantically typed.
Pre-check the receiver expression so method dispatch sees its real return type.
"""

from __future__ import annotations

from . import ast_nodes as A
from .sema import SemaAnalyzer


_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr


def _check_expr_with_chained_receivers(self: SemaAnalyzer, expression, scope) -> None:
    if isinstance(expression, A.MethodCall) and isinstance(
        expression.obj,
        (A.MethodCall, A.Call, A.Attr, A.Subscript),
    ):
        _ORIGINAL_CHECK_EXPR(self, expression.obj, scope)
    _ORIGINAL_CHECK_EXPR(self, expression, scope)


if not getattr(SemaAnalyzer, "_asmpython_chained_receiver_patch", False):
    SemaAnalyzer._check_expr = _check_expr_with_chained_receivers
    SemaAnalyzer._asmpython_chained_receiver_patch = True
