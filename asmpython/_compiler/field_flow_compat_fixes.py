"""Whole-program parameter and field-flow inference for native object layouts.

Compiled programs know every constructor call site and every statically lowered
property. Resolve those surfaces in dependency order before ordinary semantic
analysis:

1. normalize receivers and lower static descriptors,
2. specialize finite class-valued parameters,
3. infer function/method returns,
4. infer constructor parameter types from call sites,
5. infer instance field and collection element types.
"""

from __future__ import annotations

from . import ast_nodes as A
from .analysis_compat_fixes import _infer_dynamic_returns
from .dynamic_parameter_compat_fixes import _mark_dynamic_parameters
from .language_compat_fixes import (
    _lower_static_data_descriptors,
    _normalize_method_receivers,
)
from .object_flow_compat_fixes import _walk_expression, _walk_statements
from .sema import SemaAnalyzer
from .type_parameter_compat_fixes import _lower_type_parameter_specializations


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_NONE = "<none>"
_UNKNOWN = "<unknown>"
_BUILTIN_TYPES = {
    "any",
    "bool",
    "int",
    "float",
    "str",
    "list",
    "dict",
    "tuple",
    "set",
}


def _annotation_name(annotation) -> "str | None":
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, tuple) and annotation:
        return annotation[0] if isinstance(annotation[0], str) else None
    return None


def _annotation_element(annotation) -> "str | None":
    if isinstance(annotation, tuple) and len(annotation) > 1:
        return annotation[1] if isinstance(annotation[1], str) else None
    return None


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
    concrete = [
        value
        for value in types
        if value not in (_NONE, _UNKNOWN, "any") and value not in _BUILTIN_TYPES
    ]
    if not concrete:
        return None
    chains = [_ancestor_chain(value, parents) for value in concrete]
    for candidate in chains[0]:
        if all(candidate in chain for chain in chains[1:]):
            return candidate
    return None


def _common_type(types: list[str], parents: dict) -> "str | None":
    concrete = [value for value in types if value not in (_NONE, _UNKNOWN, "any")]
    if not concrete:
        return None
    class_type = _common_class(concrete, parents)
    if class_type is not None:
        return class_type
    unique = set(concrete)
    if len(unique) == 1:
        return concrete[0]
    if unique.issubset({"bool", "int", "float"}):
        return "float" if "float" in unique else "int"
    return "any"


def _return_tables(mod: A.Module) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    function_returns: dict[str, str] = {}
    method_returns: dict[tuple[str, str], str] = {}
    for function in mod.funcs:
        value = _annotation_name(function.ret_type)
        if value is not None:
            function_returns[function.name] = value
    for owner in mod.classes:
        for method in owner.methods:
            value = _annotation_name(method.ret_type)
            if value is not None:
                method_returns[(owner.name, method.name)] = value
    return function_returns, method_returns


def _field_tables(mod: A.Module) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], str]]:
    field_types: dict[tuple[str, str], str] = {}
    field_elements: dict[tuple[str, str], str] = {}

    for owner in mod.classes:
        for field_name, annotation, initializer in owner.class_vars:
            field_type = _annotation_name(annotation)
            if field_type is None:
                field_type = _literal_type(initializer, {}, set(), {}, {}, {}, {})
            if field_type not in (_UNKNOWN, _NONE, None):
                field_types[(owner.name, field_name)] = field_type
                element = _annotation_element(annotation)
                if element is not None:
                    field_elements[(owner.name, field_name)] = element

        for method in owner.methods:
            decorators = list(getattr(method, "decorators", []) or [])
            if "property" in decorators:
                value = _annotation_name(method.ret_type)
                if value is not None:
                    field_types[(owner.name, method.name)] = value
                    element = _annotation_element(method.ret_type)
                    if element is not None:
                        field_elements[(owner.name, method.name)] = element
            if method.name != "__init__":
                continue
            for node in _walk_statements(method.body):
                if not (
                    isinstance(node, A.AttrAssign)
                    and isinstance(node.obj, A.Name)
                    and node.obj.name == "self"
                ):
                    continue
                value = _annotation_name(node.annot)
                if value is not None:
                    field_types[(owner.name, node.name)] = value
                    element = _annotation_element(node.annot)
                    if element is not None:
                        field_elements[(owner.name, node.name)] = element
    return field_types, field_elements


def _lookup_method_return(
    receiver_type: str,
    method_name: str,
    method_returns: dict,
    parents: dict,
) -> "str | None":
    if receiver_type not in (_UNKNOWN, _NONE, "any") and receiver_type not in _BUILTIN_TYPES:
        current: "str | None" = receiver_type
        seen: set[str] = set()
        while current is not None and current not in seen:
            seen.add(current)
            value = method_returns.get((current, method_name))
            if value is not None:
                return value
            current = parents.get(current)

    candidates = {
        value
        for (owner, name), value in method_returns.items()
        if name == method_name and value not in (None, "any")
    }
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _lookup_field_type(
    receiver_type: str,
    field_name: str,
    field_types: dict,
    parents: dict,
) -> "str | None":
    if receiver_type in (_UNKNOWN, _NONE, "any") or receiver_type in _BUILTIN_TYPES:
        return None
    current: "str | None" = receiver_type
    seen: set[str] = set()
    while current is not None and current not in seen:
        seen.add(current)
        value = field_types.get((current, field_name))
        if value is not None:
            return value
        current = parents.get(current)
    return None


def _literal_type(
    expression,
    environment: dict,
    class_names: set,
    function_returns: dict,
    method_returns: dict,
    field_types: dict,
    parents: dict,
) -> str:
    if expression is None:
        return _NONE
    if isinstance(expression, A.IntLit):
        if getattr(expression, "is_none", False):
            return _NONE
        return "bool" if getattr(expression, "is_bool", False) else "int"
    if isinstance(expression, A.FloatLit):
        return "float"
    if isinstance(expression, (A.StrLit, A.FString)):
        return "str"
    if isinstance(expression, (A.ListLit, A.Comprehension)):
        return "list"
    if isinstance(expression, (A.DictLit, A.DictComprehension)):
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
        if expression.func in function_returns:
            return function_returns[expression.func]
        if expression.func == "getattr":
            if len(expression.args) >= 3:
                return _literal_type(
                    expression.args[2],
                    environment,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            if (
                len(expression.args) >= 2
                and isinstance(expression.args[1], A.StrLit)
                and expression.args[1].value == "__name__"
            ):
                return "str"
        builtin_returns = {
            "bool": "bool",
            "int": "int",
            "float": "float",
            "str": "str",
            "list": "list",
            "dict": "dict",
            "tuple": "tuple",
            "set": "set",
            "frozenset": "set",
            "sorted": "list",
            "reversed": "list",
            "bytes": "list",
            "bytearray": "list",
            "len": "int",
            "id": "int",
            "hash": "int",
            "ord": "int",
            "chr": "str",
            "repr": "str",
            "format": "str",
            "hex": "str",
            "oct": "str",
            "bin": "str",
            "sum": "int",
        }
        return builtin_returns.get(expression.func, _UNKNOWN)
    if isinstance(expression, A.MethodCall):
        receiver = _literal_type(
            expression.obj,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        if expression.method == "get" and len(expression.args) >= 2:
            default_type = _literal_type(
                expression.args[1],
                environment,
                class_names,
                function_returns,
                method_returns,
                field_types,
                parents,
            )
            if default_type not in (_UNKNOWN, _NONE):
                return default_type
        resolved = _lookup_method_return(receiver, expression.method, method_returns, parents)
        return resolved if resolved is not None else _UNKNOWN
    if isinstance(expression, A.Attr):
        if expression.name == "__name__":
            return "str"
        receiver = _literal_type(
            expression.obj,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        resolved = _lookup_field_type(receiver, expression.name, field_types, parents)
        return resolved if resolved is not None else _UNKNOWN
    if isinstance(expression, A.BoolOp):
        left = _literal_type(
            expression.left,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        right = _literal_type(
            expression.right,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        if left in (_NONE, _UNKNOWN, "any"):
            return right
        if right in (_NONE, _UNKNOWN, "any"):
            return left
        return left if left == right else _common_type([left, right], parents) or _UNKNOWN
    if isinstance(expression, A.IfExp):
        left = _literal_type(
            expression.body,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        right = _literal_type(
            expression.orelse,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        return _common_type([left, right], parents) or _UNKNOWN
    if isinstance(expression, A.Compare):
        return "bool"
    if isinstance(expression, A.UnaryOp):
        return _literal_type(
            expression.operand,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
    if isinstance(expression, A.BinOp):
        left = _literal_type(
            expression.left,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        right = _literal_type(
            expression.right,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        return _common_type([left, right], parents) or _UNKNOWN
    if isinstance(expression, A.Subscript):
        source = _literal_type(
            expression.obj,
            environment,
            class_names,
            function_returns,
            method_returns,
            field_types,
            parents,
        )
        return "str" if source == "str" else _UNKNOWN
    return _UNKNOWN


def _all_bodies(mod: A.Module) -> list[list]:
    result: list[list] = [mod.body]
    result.extend(function.body for function in mod.funcs)
    for owner in mod.classes:
        result.extend(method.body for method in owner.methods)
    return result


def _constructor_owner(owner_name: str, classes: dict):
    current = classes.get(owner_name)
    seen: set[str] = set()
    while current is not None and current.name not in seen:
        seen.add(current.name)
        initializer = next(
            (method for method in current.methods if method.name == "__init__"),
            None,
        )
        if initializer is not None:
            return current, initializer
        current = classes.get(current.parent)
    return None, None


def _constructor_calls(
    mod: A.Module,
    function_returns: dict,
    method_returns: dict,
    field_types: dict,
    parents: dict,
) -> dict[str, list]:
    classes = {owner.name: owner for owner in mod.classes}
    class_names = set(classes)
    calls: dict[str, list] = {name: [] for name in class_names}

    for body in _all_bodies(mod):
        environment: dict[str, str] = {}
        for node in _walk_statements(body):
            if isinstance(node, A.Assign) and isinstance(node.target, str):
                environment[node.target] = _literal_type(
                    node.value,
                    environment,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            for name in ("expr", "value", "test", "iter", "target", "obj"):
                expression = getattr(node, name, None)
                if expression is None or isinstance(expression, str):
                    continue
                for nested in _walk_expression(expression):
                    if isinstance(nested, A.Call) and nested.func in class_names:
                        calls[nested.func].append((nested, dict(environment)))
    return calls


def _constructor_specializations(
    mod: A.Module,
    function_returns: dict,
    method_returns: dict,
    field_types: dict,
    parents: dict,
) -> dict[tuple[str, str], list[str]]:
    classes = {owner.name: owner for owner in mod.classes}
    class_names = set(classes)
    calls = _constructor_calls(
        mod,
        function_returns,
        method_returns,
        field_types,
        parents,
    )
    result: dict[tuple[str, str], list[str]] = {}

    for called_class, class_calls in calls.items():
        initializer_owner, initializer = _constructor_owner(called_class, classes)
        if initializer is None:
            continue
        params = list(initializer.params[1:])
        defaults = list(initializer.defaults[1:]) if initializer.defaults else [None] * len(params)

        for call, environment in class_calls:
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
                value_type = _literal_type(
                    expression,
                    environment,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
                result.setdefault((initializer_owner.name, parameter), []).append(value_type)
    return result


def _annotate_constructor_parameters(
    mod: A.Module,
    specializations: dict,
    parents: dict,
) -> None:
    for owner in mod.classes:
        initializer = next(
            (method for method in owner.methods if method.name == "__init__"),
            None,
        )
        if initializer is None:
            continue
        while len(initializer.param_types) < len(initializer.params):
            initializer.param_types.append(None)
        for index, parameter in enumerate(initializer.params[1:], start=1):
            if initializer.param_types[index] is not None:
                continue
            inferred = _common_type(
                specializations.get((owner.name, parameter), []),
                parents,
            )
            if inferred is not None:
                initializer.param_types[index] = (inferred, None)


def _parameter_type(definition, parameter: str) -> str:
    if parameter not in definition.params:
        return _UNKNOWN
    index = definition.params.index(parameter)
    if index >= len(definition.param_types):
        return _UNKNOWN
    return _annotation_name(definition.param_types[index]) or _UNKNOWN


def _annotate_object_fields(mod: A.Module) -> None:
    if getattr(mod, "_object_fields_annotated", False):
        return
    mod._object_fields_annotated = True

    class_names = {owner.name for owner in mod.classes}
    parents = _parent_map(mod)
    function_returns, method_returns = _return_tables(mod)
    field_types, _field_elements = _field_tables(mod)
    specializations = _constructor_specializations(
        mod,
        function_returns,
        method_returns,
        field_types,
        parents,
    )
    _annotate_constructor_parameters(mod, specializations, parents)

    for owner in mod.classes:
        initializer = next(
            (method for method in owner.methods if method.name == "__init__"),
            None,
        )
        if initializer is None:
            continue

        parameter_values = {
            parameter: specializations.get((owner.name, parameter), [])
            for parameter in initializer.params[1:]
        }
        parameter_environment = {
            parameter: _parameter_type(initializer, parameter)
            for parameter in initializer.params[1:]
        }
        parameter_environment["self"] = owner.name

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

            inferred = _literal_type(
                node.value,
                parameter_environment,
                class_names,
                function_returns,
                method_returns,
                field_types,
                parents,
            )
            if isinstance(node.value, A.Name):
                # Narrow `self.x = param` from the types seen at construction
                # call sites ONLY when `param` has no explicit annotation. An
                # explicitly-annotated parameter -- above all `object`/`Any`
                # (declared type "any") -- must keep its declared type: a
                # `def __init__(self, v: object)` deliberately accepts any
                # kind, so a `T("hello")` call site must NOT pin field `v` to
                # "str". Doing so drops the box on `self.v`, and a later
                # `x: str = t.v` reads the raw box cell as a string (garbage).
                # `_parameter_type` returns the declared annotation name, or
                # _UNKNOWN for a genuinely unannotated parameter -- only the
                # latter is safe to specialize from call sites.
                declared = parameter_environment.get(node.value.name, _UNKNOWN)
                if declared == _UNKNOWN:
                    inferred = _common_type(
                        parameter_values.get(node.value.name, []),
                        parents,
                    ) or inferred
                else:
                    inferred = declared
            elif isinstance(node.value, A.BoolOp):
                names = [
                    expression.name
                    for expression in (node.value.left, node.value.right)
                    if isinstance(expression, A.Name)
                ]
                values: list[str] = []
                for name in names:
                    values.extend(parameter_values.get(name, []))
                values.extend(
                    value
                    for value in (
                        _literal_type(
                            node.value.left,
                            parameter_environment,
                            class_names,
                            function_returns,
                            method_returns,
                            field_types,
                            parents,
                        ),
                        _literal_type(
                            node.value.right,
                            parameter_environment,
                            class_names,
                            function_returns,
                            method_returns,
                            field_types,
                            parents,
                        ),
                    )
                    if value not in (_UNKNOWN, _NONE)
                )
                inferred = _common_type(values, parents) or inferred

            if inferred not in (_UNKNOWN, _NONE, None):
                # Don't clobber an EXISTING user annotation with the coarse
                # `(inferred, None)` shape: `self.counts: dict[str, int] = {}`
                # already carries the precise value kind (`int`), and
                # overwriting it with `("dict", None)` drops that -- making
                # every `self.counts[k]` read/store degrade to an "any" value
                # kind (a str value formats as a raw pointer, an int value gets
                # spuriously boxed, etc.). Only fill in a MISSING annotation
                # (an unannotated `self.x = <expr>`), where this inference is
                # the only type information available.
                if getattr(node, "annot", None) is None:
                    node.annot = (inferred, None)
                field_types[(owner.name, node.name)] = inferred

        list_elements: dict[str, list[str]] = {name: [] for name in empty_lists}
        for method in owner.methods:
            for node in _walk_statements(method.body):
                if not (
                    isinstance(node, A.MethodCall)
                    and node.method == "append"
                    and node.args
                    and isinstance(node.obj, A.Attr)
                    and isinstance(node.obj.obj, A.Name)
                    and node.obj.obj.name == "self"
                    and node.obj.name in list_elements
                ):
                    continue
                argument = node.args[0]
                if isinstance(argument, A.Name):
                    element = _parameter_type(method, argument.name)
                else:
                    element = _literal_type(
                        argument,
                        {},
                        class_names,
                        function_returns,
                        method_returns,
                        field_types,
                        parents,
                    )
                list_elements[node.obj.name].append(element)

        for name, assignment in empty_lists.items():
            observed = [value for value in list_elements[name] if value != _UNKNOWN]
            if not observed:
                if assignment.annot is None:
                    assignment.annot = ("list", "any")
                continue
            element = _common_type(observed, parents) or "any"
            assignment.annot = ("list", element)

        for assignment in empty_dicts.values():
            if assignment.annot is None:
                assignment.annot = ("dict", "any")


def _analyze_with_field_flow(self: SemaAnalyzer) -> None:
    _normalize_method_receivers(self.mod)
    _lower_static_data_descriptors(self.mod)
    _lower_type_parameter_specializations(self.mod)
    _mark_dynamic_parameters(self.mod)
    _infer_dynamic_returns(self.mod)
    _annotate_object_fields(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_field_flow_patch", False):
    SemaAnalyzer.analyze = _analyze_with_field_flow
    SemaAnalyzer._asmpython_field_flow_patch = True
