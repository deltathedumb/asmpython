"""Whole-program field-flow inference for native object layouts.

Compiled programs know every constructor call site. Use that information to
specialize optional constructor parameters and fields initialized through common
Python fallback patterns such as ``value or DefaultClass()``. Empty container
fields also inherit element/value kinds from later append/update operations.
"""

from __future__ import annotations

from . import ast_nodes as A
from .dynamic_parameter_compat_fixes import _mark_dynamic_parameters
from .language_compat_fixes import _normalize_method_receivers
from .object_flow_compat_fixes import _walk_expression, _walk_statements
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_NONE = "<none>"
_UNKNOWN = "<unknown>"


def _simple_type(expression, environment: dict, class_names: set) -> str:
    if expression is None:
        return _NONE
    if isinstance(expression, A.IntLit):
        if getattr(expression, "is_none", False):
            return _NONE
        return "bool" if getattr(expression, "is_bool", False) else "int"
    if isinstance(expression, A.FloatLit):
        return "float"
    if isinstance(expression, A.StrLit) or isinstance(expression, A.FString):
        return "str"
    if isinstance(expression, A.ListLit):
        return "list"
    if isinstance(expression, A.DictLit):
        return "dict"
    if isinstance(expression, A.TupleLit):
        return "tuple"
    if isinstance(expression, A.SetLit):
        return "set"
    if isinstance(expression, A.Name):
        return environment.get(expression.name, _UNKNOWN)
    if isinstance(expression, A.Call):
        if expression.func in class_names:
            return expression.func
        return _UNKNOWN
    if isinstance(expression, A.BoolOp):
        left = _simple_type(expression.left, environment, class_names)
        right = _simple_type(expression.right, environment, class_names)
        if left == _NONE:
            return right
        if right == _NONE:
            return left
        return left if left == right else _UNKNOWN
    return _UNKNOWN


def _parent_map(mod: A.Module) -> dict[str, "str | None"]:
    return {owner.name: owner.parent for owner in mod.classes}


def _ancestor_chain(name: str, parents: dict) -> list[str]:
    result: list[str] = []
    current: "str | None" = name
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        result.append(current)
        current = parents.get(current)
    return result


def _common_class(types: list[str], parents: dict) -> "str | None":
    concrete = [value for value in types if value not in (_NONE, _UNKNOWN, "any")]
    if not concrete:
        return None
    chains = [_ancestor_chain(value, parents) for value in concrete]
    for candidate in chains[0]:
        if all(candidate in chain for chain in chains[1:]):
            return candidate
    return None


def _direct_function_bodies(mod: A.Module) -> list[list]:
    functions = {function.name: function for function in mod.funcs}
    queued = list(mod.body)
    bodies: list[list] = [mod.body]
    seen_functions: set[str] = set()

    index = 0
    while index < len(queued):
        statement = queued[index]
        index += 1
        expressions = []
        for name in ("expr", "value", "test", "iter", "target", "obj"):
            value = getattr(statement, name, None)
            if value is not None and not isinstance(value, str):
                expressions.extend(_walk_expression(value))
        for expression in expressions:
            if (
                isinstance(expression, A.Call)
                and expression.func in functions
                and expression.func not in seen_functions
            ):
                seen_functions.add(expression.func)
                body = functions[expression.func].body
                bodies.append(body)
                queued.extend(body)
        for name in ("then", "orelse", "body", "handler", "else_body", "finally_body"):
            nested = getattr(statement, name, None)
            if isinstance(nested, list):
                queued.extend(nested)
    return bodies


def _constructor_calls(mod: A.Module) -> dict[str, list]:
    class_names = {owner.name for owner in mod.classes}
    calls: dict[str, list] = {name: [] for name in class_names}

    for body in _direct_function_bodies(mod):
        environment: dict[str, str] = {}
        for node in _walk_statements(body):
            if isinstance(node, A.Assign) and isinstance(node.target, str):
                value_type = _simple_type(node.value, environment, class_names)
                environment[node.target] = value_type
            for name in ("expr", "value", "test", "iter", "target", "obj"):
                expression = getattr(node, name, None)
                if expression is None or isinstance(expression, str):
                    continue
                for nested in _walk_expression(expression):
                    if isinstance(nested, A.Call) and nested.func in class_names:
                        calls[nested.func].append((nested, dict(environment)))
    return calls


def _constructor_specializations(mod: A.Module) -> dict[tuple[str, str], list[str]]:
    class_names = {owner.name for owner in mod.classes}
    calls = _constructor_calls(mod)
    result: dict[tuple[str, str], list[str]] = {}

    for owner in mod.classes:
        initializer = next((method for method in owner.methods if method.name == "__init__"), None)
        if initializer is None:
            continue
        params = list(initializer.params[1:])
        defaults = list(initializer.defaults[1:]) if initializer.defaults else [None] * len(params)

        for call, environment in calls.get(owner.name, []):
            values: list = [None] * len(params)
            for index, argument in enumerate(call.args):
                if index < len(values):
                    values[index] = argument
            for keyword, argument in call.kwargs:
                if keyword in params:
                    values[params.index(keyword)] = argument
            for index, parameter in enumerate(params):
                expression = values[index]
                if expression is None and index < len(defaults):
                    expression = defaults[index]
                value_type = _simple_type(expression, environment, class_names)
                result.setdefault((owner.name, parameter), []).append(value_type)
    return result


def _parameter_type(definition, parameter: str) -> str:
    if parameter not in definition.params:
        return _UNKNOWN
    index = definition.params.index(parameter)
    if index >= len(definition.param_types):
        return _UNKNOWN
    annotation = definition.param_types[index]
    if isinstance(annotation, tuple) and annotation and isinstance(annotation[0], str):
        return annotation[0]
    if isinstance(annotation, str):
        return annotation
    return _UNKNOWN


def _annotate_object_fields(mod: A.Module) -> None:
    if getattr(mod, "_object_fields_annotated", False):
        return
    mod._object_fields_annotated = True

    class_names = {owner.name for owner in mod.classes}
    parents = _parent_map(mod)
    specializations = _constructor_specializations(mod)

    for owner in mod.classes:
        initializer = next((method for method in owner.methods if method.name == "__init__"), None)
        if initializer is None:
            continue

        parameter_values = {
            parameter: specializations.get((owner.name, parameter), [])
            for parameter in initializer.params[1:]
        }

        empty_lists: dict[str, A.AttrAssign] = {}
        empty_dicts: dict[str, A.AttrAssign] = {}

        for node in _walk_statements(initializer.body):
            if not (
                isinstance(node, A.AttrAssign)
                and isinstance(node.obj, A.Name)
                and node.obj.name == "self"
            ):
                continue

            if isinstance(node.value, A.ListLit) and not node.value.elems:
                empty_lists[node.name] = node
            if isinstance(node.value, A.DictLit) and not node.value.keys:
                empty_dicts[node.name] = node

            inferred = None
            if isinstance(node.value, A.Call) and node.value.func in class_names:
                inferred = node.value.func
            elif isinstance(node.value, A.Name):
                values = parameter_values.get(node.value.name, [])
                inferred = _common_class(values, parents)
            elif isinstance(node.value, A.BoolOp):
                left = node.value.left
                right = node.value.right
                parameter = None
                fallback = None
                if isinstance(left, A.Name) and isinstance(right, A.Call):
                    parameter, fallback = left.name, right.func
                elif isinstance(right, A.Name) and isinstance(left, A.Call):
                    parameter, fallback = right.name, left.func
                if fallback in class_names:
                    values = list(parameter_values.get(parameter, []))
                    values.append(fallback)
                    inferred = _common_class(values, parents)
                    if inferred is None and all(value == _NONE for value in values[:-1]):
                        inferred = fallback

            if inferred is not None:
                node.annot = (inferred, None)

        # Infer empty-list element kinds from later ``self.field.append(value)``.
        list_elements: dict[str, list[str]] = {name: [] for name in empty_lists}
        for method in owner.methods:
            for node in _walk_statements(method.body):
                if not isinstance(node, A.MethodCall) or node.method != "append" or not node.args:
                    continue
                if not (
                    isinstance(node.obj, A.Attr)
                    and isinstance(node.obj.obj, A.Name)
                    and node.obj.obj.name == "self"
                    and node.obj.name in list_elements
                ):
                    continue
                argument = node.args[0]
                if isinstance(argument, A.Name):
                    element = _parameter_type(method, argument.name)
                else:
                    element = _simple_type(argument, {}, class_names)
                list_elements[node.obj.name].append(element)

        for name, assignment in empty_lists.items():
            observed = [value for value in list_elements[name] if value != _UNKNOWN]
            if not observed:
                continue
            concrete = _common_class(observed, parents)
            if concrete is not None:
                assignment.annot = ("list", concrete)
            elif len(set(observed)) == 1:
                assignment.annot = ("list", observed[0])
            else:
                assignment.annot = ("list", "any")

        # Empty dictionaries receiving opaque values must not retain the legacy
        # integer value default.
        for assignment in empty_dicts.values():
            if assignment.annot is None:
                assignment.annot = ("dict", "any")


def _analyze_with_field_flow(self: SemaAnalyzer) -> None:
    _normalize_method_receivers(self.mod)
    _mark_dynamic_parameters(self.mod)
    _annotate_object_fields(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_field_flow_patch", False):
    SemaAnalyzer.analyze = _analyze_with_field_flow
    SemaAnalyzer._asmpython_field_flow_patch = True
