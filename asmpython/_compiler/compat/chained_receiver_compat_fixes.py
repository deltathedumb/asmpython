"""Type non-trivial method receivers before resolving the outer call.

Descriptor lowering can turn ``obj.property.method()`` into nested method-call
nodes. The outer call historically captured the parser's integer fallback
before the inner getter's method signature had been applied. Resolve and stamp
that inner return type before dispatching the outer call.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..sema import SemaAnalyzer


_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr


def _resolved_method_return(self: SemaAnalyzer, expression: A.MethodCall):
    receiver_type = A.expr_type(expression.obj)
    if receiver_type.startswith("instance:"):
        class_name = receiver_type.split(":", 1)[1]
    elif receiver_type in self.classes:
        class_name = receiver_type
    else:
        return None
    resolved = self._resolve_method(class_name, expression.method)
    if resolved is None:
        return None
    return getattr(resolved[1], "ret_type", None)


def _stamp_method_return(self: SemaAnalyzer, expression: A.MethodCall) -> None:
    return_type = _resolved_method_return(self, expression)
    if not isinstance(return_type, tuple) or not return_type:
        return
    expression.inferred_type = return_type[0]
    if len(return_type) > 1 and return_type[1] is not None:
        expression.list_el_type = return_type[1]
    if len(return_type) > 2 and return_type[2] is not None:
        expression.value_type = return_type[2]


def _check_expr_with_chained_receivers(self: SemaAnalyzer, expression, scope) -> None:
    if isinstance(expression, A.MethodCall) and isinstance(
        expression.obj,
        (A.MethodCall, A.Call, A.Attr, A.Subscript),
    ):
        inner = expression.obj
        if isinstance(inner, A.MethodCall):
            if isinstance(inner.obj, (A.MethodCall, A.Call, A.Attr, A.Subscript)):
                _ORIGINAL_CHECK_EXPR(self, inner.obj, scope)
            _stamp_method_return(self, inner)
        else:
            _ORIGINAL_CHECK_EXPR(self, inner, scope)
    _ORIGINAL_CHECK_EXPR(self, expression, scope)


if not getattr(SemaAnalyzer, "_asmpython_chained_receiver_patch", False):
    SemaAnalyzer._check_expr = _check_expr_with_chained_receivers
    SemaAnalyzer._asmpython_chained_receiver_patch = True
