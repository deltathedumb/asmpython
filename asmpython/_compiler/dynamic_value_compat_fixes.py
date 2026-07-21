"""Compatibility for statically recoverable dynamic Python values.

The whole-program return pass and semantic checker historically disagreed on a
few common dynamic forms. This module aligns return inference for ``dict.get``,
``getattr`` and ``.__name__`` with the later field-flow pass, and folds direct
``str(UserClass)`` calls to a stable class representation before codegen.
"""

from __future__ import annotations

from . import analysis_compat_fixes as analysis
from . import ast_nodes as A
from .sema import SemaAnalyzer


_ORIGINAL_EXPRESSION_ANNOTATION = analysis._expression_annotation
_ORIGINAL_CHECK_CALL = SemaAnalyzer._check_call


def _expression_annotation_with_dynamic_values(
    expression,
    environment: dict,
    owner_name,
    function_returns: dict,
    method_returns: dict,
    class_names: set,
    parents: dict,
) -> str:
    if isinstance(expression, A.Attr) and expression.name == "__name__":
        return "str"

    if isinstance(expression, A.Call) and expression.func == "getattr":
        if len(expression.args) >= 3:
            return analysis._expression_annotation(
                expression.args[2],
                environment,
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
        if (
            len(expression.args) >= 2
            and isinstance(expression.args[1], A.StrLit)
            and expression.args[1].value == "__name__"
        ):
            return "str"

    if (
        isinstance(expression, A.MethodCall)
        and expression.method == "get"
        and len(expression.args) >= 2
    ):
        default_type = analysis._expression_annotation(
            expression.args[1],
            environment,
            owner_name,
            function_returns,
            method_returns,
            class_names,
            parents,
        )
        if default_type not in (analysis._UNKNOWN, analysis._NONE):
            return default_type

    return _ORIGINAL_EXPRESSION_ANNOTATION(
        expression,
        environment,
        owner_name,
        function_returns,
        method_returns,
        class_names,
        parents,
    )


def _check_call_with_static_class_str(self: SemaAnalyzer, expression, scope) -> None:
    if (
        isinstance(expression, A.Call)
        and expression.func == "str"
        and len(expression.args) == 1
        and isinstance(expression.args[0], A.Name)
        and expression.args[0].name in self.classes
    ):
        class_name = expression.args[0].name
        expression.args[0] = A.StrLit(
            value="<class '" + class_name + "'>",
            pos=expression.args[0].pos,
        )
    _ORIGINAL_CHECK_CALL(self, expression, scope)


analysis._expression_annotation = _expression_annotation_with_dynamic_values

if not getattr(SemaAnalyzer, "_asmpython_dynamic_value_patch", False):
    SemaAnalyzer._check_call = _check_call_with_static_class_str
    SemaAnalyzer._asmpython_dynamic_value_patch = True
