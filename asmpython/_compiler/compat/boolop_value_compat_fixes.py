"""Preserve Python value types through ``and`` and ``or`` expressions.

Python boolean operators return one of their operands rather than coercing the
result to ``bool``. The core semantic pass checked both operands but left the
BoolOp node at the parser's ``int`` fallback. Consequently ordinary patterns
such as ``platform or sys.platform`` were later rejected as integer values.

Use the same compatibility rule already applied to conditional expressions:
``int`` doubles as the unknown/None sentinel, so a concrete opposite operand
wins. Two different concrete kinds remain opaque because either may be returned
at runtime. The shared AST type reader must also honor that semantic stamp;
otherwise assignments immediately recompute the old fallback from the operands.
"""

from __future__ import annotations

from .. import ast_nodes as A
from ..sema import SemaAnalyzer


_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr
_ORIGINAL_EXPR_TYPE = A.expr_type


def _expr_type_with_boolop_values(expression) -> str:
    if isinstance(expression, A.BoolOp):
        inferred = getattr(expression, "inferred_type", None)
        if inferred not in (None, "int"):
            return inferred
    return _ORIGINAL_EXPR_TYPE(expression)


def _copy_collection_shape(expression, source) -> None:
    result_type = A.expr_type(expression)
    if result_type == "list":
        expression.list_el_type = getattr(source, "list_el_type", "int")
        expression.el_value_type = getattr(source, "el_value_type", "int")
        expression.tuple_elem_types = list(
            getattr(source, "tuple_elem_types", []) or []
        )
    elif result_type == "dict":
        expression.value_type = getattr(source, "value_type", "int")
        expression.inner_value_type = getattr(source, "inner_value_type", "int")
        expression.value_tuple_elem_types = list(
            getattr(source, "value_tuple_elem_types", []) or []
        )
    elif result_type == "tuple":
        expression.tuple_elem_types = list(
            getattr(source, "tuple_elem_types", []) or []
        )


def _check_expr_with_boolop_values(self: SemaAnalyzer, expression, scope) -> None:
    _ORIGINAL_CHECK_EXPR(self, expression, scope)
    if not isinstance(expression, A.BoolOp):
        return

    left_type = A.expr_type(expression.left)
    right_type = A.expr_type(expression.right)
    source = None

    if left_type == right_type:
        result_type = left_type
        source = expression.left
    elif left_type == "int":
        result_type = right_type
        source = expression.right
    elif right_type == "int":
        result_type = left_type
        source = expression.left
    elif "any" in (left_type, right_type):
        result_type = "any"
    else:
        result_type = "any"

    expression.inferred_type = result_type
    if source is not None:
        _copy_collection_shape(expression, source)


if not getattr(SemaAnalyzer, "_asmpython_boolop_value_patch", False):
    A.expr_type = _expr_type_with_boolop_values
    SemaAnalyzer._check_expr = _check_expr_with_boolop_values
    SemaAnalyzer._asmpython_boolop_value_patch = True
