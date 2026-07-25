"""Usage-based inference for unannotated dynamic Python parameters.

A parameter with no annotation or informative call-site evidence must not be
assumed to be an integer when its body clearly uses it as an object, iterable,
callable, class object, or container. Mark those parameters as ``any`` before
normal semantic analysis; numeric-only parameters retain the existing numeric
inference path.
"""

from __future__ import annotations

from . import ast_nodes as A
from .object_flow_compat_fixes import _walk_expression, _walk_statements
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _name_is(expression, parameter: str) -> bool:
    return isinstance(expression, A.Name) and expression.name == parameter


def _parameter_has_dynamic_usage(definition, parameter: str) -> bool:
    for node in _walk_statements(definition.body):
        if isinstance(node, A.MethodCall) and _name_is(node.obj, parameter):
            return True
        if isinstance(node, A.Attr) and _name_is(node.obj, parameter):
            return True
        if isinstance(node, A.Subscript) and _name_is(node.obj, parameter):
            return True
        if isinstance(node, A.For) and _name_is(node.iter, parameter):
            return True
        if isinstance(node, A.Call):
            if node.func == parameter:
                return True
            if node.func in (
                "isinstance",
                "issubclass",
                "iter",
                "next",
                "list",
                "dict",
                "tuple",
                "set",
                "vars",
                "dir",
                "getattr",
                "setattr",
                "hasattr",
                "callable",
                "type",
            ) and any(_name_is(argument, parameter) for argument in node.args):
                return True

        for name in ("expr", "value", "test", "iter", "target", "obj"):
            expression = getattr(node, name, None)
            if expression is None or isinstance(expression, str):
                continue
            for nested in _walk_expression(expression):
                if isinstance(nested, A.MethodCall) and _name_is(nested.obj, parameter):
                    return True
                if isinstance(nested, A.Attr) and _name_is(nested.obj, parameter):
                    return True
                if isinstance(nested, A.Subscript) and _name_is(nested.obj, parameter):
                    return True
                if isinstance(nested, A.Call):
                    if nested.func == parameter:
                        return True
                    if nested.func in (
                        "isinstance",
                        "issubclass",
                        "type",
                    ) and any(
                        _name_is(argument, parameter) for argument in nested.args
                    ):
                        return True
    return False


def _mark_dynamic_parameters(mod: A.Module) -> None:
    if getattr(mod, "_dynamic_parameters_marked", False):
        return
    mod._dynamic_parameters_marked = True

    definitions: list[tuple[object, int]] = []
    for function in mod.funcs:
        definitions.append((function, 0))
    for owner in mod.classes:
        for method in owner.methods:
            decorators = list(getattr(method, "decorators", []) or [])
            start = 0 if "staticmethod" in decorators else 1
            definitions.append((method, start))

    for definition, start in definitions:
        params = list(definition.params)
        param_types = list(definition.param_types)
        while len(param_types) < len(params):
            param_types.append(None)

        changed = False
        for index in range(start, len(params)):
            if param_types[index] is not None:
                continue
            parameter = params[index]
            if _parameter_has_dynamic_usage(definition, parameter):
                param_types[index] = ("any", None)
                changed = True
        if changed:
            definition.param_types = param_types


def _analyze_with_dynamic_parameters(self: SemaAnalyzer) -> None:
    _mark_dynamic_parameters(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_dynamic_parameter_patch", False):
    SemaAnalyzer.analyze = _analyze_with_dynamic_parameters
    SemaAnalyzer._asmpython_dynamic_parameter_patch = True
