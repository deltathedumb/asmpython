"""Compatibility fixes for semantic analysis of whole-program projects.

The whole-program loader merges every reachable module's definitions into one
module, including helpers which the entry point never calls. asmpython already
suppresses semantic failures in unreachable bundled-stdlib bodies; applying the
same conservative reachability rule to ordinary project modules prevents an
unused optional backend from blocking an otherwise native-compilable program.
Reachable functions and methods remain fully checked.

This module also supplies a fixed-point return inference pass for ordinary
unannotated Python functions. The native compiler historically treated every
unknown return as ``int``; Python's correct conservative type is dynamic/opaque.
The pass preserves concrete constructor/container/scalar returns when knowable
and uses ``any`` when a value is genuinely dynamic.
"""

from __future__ import annotations

from . import ast_nodes as A
from .language_compat_fixes import (
    _lower_static_data_descriptors,
    _normalize_method_receivers,
)
from .sema import SemaAnalyzer, _syntactic_reachable_names


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_NONE = "<none>"
_UNKNOWN = "<unknown>"


def _annotation_name(annotation) -> "str | None":
    if annotation is None:
        return None
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, tuple) and annotation:
        head = annotation[0]
        return head if isinstance(head, str) else None
    return None


def _literal_annotation(expression, class_names: set) -> "str | None":
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
    if isinstance(expression, A.ListLit) or isinstance(expression, A.Comprehension):
        return "list"
    if isinstance(expression, A.DictLit) or isinstance(expression, A.DictComprehension):
        return "dict"
    if isinstance(expression, A.TupleLit):
        return "tuple"
    if isinstance(expression, A.SetLit):
        return "set"
    if isinstance(expression, A.Call) and expression.func in class_names:
        return expression.func
    return None


def _merge_annotations(values: list[str]) -> "str | None":
    if not values:
        return None
    unique = set(values)
    if unique == {_NONE}:
        # A pure ``return None`` function is still a procedure; leave its native
        # return convention unchanged rather than pretending it yields an int.
        return None
    if _UNKNOWN in unique:
        return "any"
    if _NONE in unique:
        unique.remove(_NONE)
        if not unique:
            return None
        return "any"
    if len(unique) == 1:
        for value in unique:
            return value
    # Numeric widening is safe and useful for mixed int/float branches.
    if unique.issubset({"int", "bool", "float"}):
        return "float" if "float" in unique else "int"
    return "any"


def _parent_table(mod: A.Module) -> dict[str, "str | None"]:
    return {owner.name: owner.parent for owner in mod.classes}


def _resolve_method_return(
    receiver_type: "str | None",
    method_name: str,
    method_returns: dict,
    parents: dict,
) -> "str | None":
    if receiver_type is not None and receiver_type not in (
        "any",
        "int",
        "float",
        "str",
        "list",
        "dict",
        "tuple",
        "set",
        "bool",
    ):
        current = receiver_type
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
        if name == method_name and value is not None and value != "any"
    }
    if len(candidates) == 1:
        for value in candidates:
            return value
    if not candidates and any(
        name == method_name and value == "any"
        for (_owner, name), value in method_returns.items()
    ):
        return "any"
    return None


def _expression_annotation(
    expression,
    environment: dict,
    owner_name: "str | None",
    function_returns: dict,
    method_returns: dict,
    class_names: set,
    parents: dict,
) -> str:
    literal = _literal_annotation(expression, class_names)
    if literal is not None:
        return literal

    if isinstance(expression, A.Name):
        if owner_name is not None and expression.name == "self":
            return owner_name
        if expression.name in class_names:
            # A class object is not itself an instance value.
            return "any"
        return environment.get(expression.name, _UNKNOWN)

    if isinstance(expression, A.Call):
        if expression.func in class_names:
            return expression.func
        if expression.func in function_returns:
            return function_returns[expression.func]
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
        receiver = _expression_annotation(
            expression.obj,
            environment,
            owner_name,
            function_returns,
            method_returns,
            class_names,
            parents,
        )
        resolved = _resolve_method_return(
            None if receiver in (_UNKNOWN, _NONE) else receiver,
            expression.method,
            method_returns,
            parents,
        )
        return resolved if resolved is not None else _UNKNOWN

    if isinstance(expression, A.Attr):
        return _UNKNOWN

    if isinstance(expression, A.IfExp):
        return _merge_annotations(
            [
                _expression_annotation(
                    expression.body,
                    environment,
                    owner_name,
                    function_returns,
                    method_returns,
                    class_names,
                    parents,
                ),
                _expression_annotation(
                    expression.orelse,
                    environment,
                    owner_name,
                    function_returns,
                    method_returns,
                    class_names,
                    parents,
                ),
            ]
        ) or _UNKNOWN

    if isinstance(expression, A.BoolOp):
        values = [
            _expression_annotation(
                expression.left,
                environment,
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            ),
            _expression_annotation(
                expression.right,
                environment,
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            ),
        ]
        return _merge_annotations(values) or _UNKNOWN

    if isinstance(expression, A.Compare):
        return "bool"

    if isinstance(expression, A.UnaryOp):
        return _expression_annotation(
            expression.operand,
            environment,
            owner_name,
            function_returns,
            method_returns,
            class_names,
            parents,
        )

    if isinstance(expression, A.BinOp):
        if expression.op == "/":
            # TRUE DIVISION always yields a float in Python 3, whatever the
            # operands are -- merging the operand types instead inferred `int`
            # for `def half(x): return x / 2`, and the caller then printed the
            # returned double's raw bits as a pointer-sized integer.
            return "float"
        left = _expression_annotation(
            expression.left,
            environment,
            owner_name,
            function_returns,
            method_returns,
            class_names,
            parents,
        )
        right = _expression_annotation(
            expression.right,
            environment,
            owner_name,
            function_returns,
            method_returns,
            class_names,
            parents,
        )
        return _merge_annotations([left, right]) or _UNKNOWN

    if isinstance(expression, A.Subscript):
        container = _expression_annotation(
            expression.obj,
            environment,
            owner_name,
            function_returns,
            method_returns,
            class_names,
            parents,
        )
        if container == "str":
            return "str"
        return _UNKNOWN

    return _UNKNOWN


def _seed_environment(definition, owner_name: "str | None", class_names: set) -> dict:
    environment: dict[str, str] = {}
    params = list(definition.params)
    annotations = list(definition.param_types)
    defaults = list(definition.defaults)
    decorators = list(getattr(definition, "decorators", []) or [])

    for index, name in enumerate(params):
        if owner_name is not None and index == 0 and "staticmethod" not in decorators:
            environment[name] = "any" if "classmethod" in decorators else owner_name
            continue
        annotation = _annotation_name(annotations[index] if index < len(annotations) else None)
        if annotation is not None:
            environment[name] = annotation
            continue
        default = defaults[index] if index < len(defaults) else None
        inferred_default = _literal_annotation(default, class_names)
        environment[name] = inferred_default if inferred_default not in (None, _NONE) else "any"
    return environment


def _merge_environments(left: dict, right: dict) -> dict:
    result = dict(left)
    for name, value in right.items():
        if name not in result:
            result[name] = value
        elif result[name] != value:
            result[name] = "any"
    return result


def _analyze_statements(
    statements: list,
    environment: dict,
    owner_name: "str | None",
    function_returns: dict,
    method_returns: dict,
    class_names: set,
    parents: dict,
) -> tuple[list[str], dict]:
    returns: list[str] = []
    current = dict(environment)

    for statement in statements:
        if isinstance(statement, A.Assign):
            if isinstance(statement.target, str):
                current[statement.target] = _expression_annotation(
                    statement.value,
                    current,
                    owner_name,
                    function_returns,
                    method_returns,
                    class_names,
                    parents,
                )
            continue

        if isinstance(statement, A.MultiAssign):
            value_type = _expression_annotation(
                statement.value,
                current,
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            for target in statement.targets:
                if isinstance(target, str):
                    current[target] = value_type
            continue

        if isinstance(statement, A.TupleAssign):
            for target in statement.targets:
                if isinstance(target, A.Name):
                    current[target.name] = "any"
                elif isinstance(target, A.StarTarget):
                    current[target.name] = "list"
            continue

        if isinstance(statement, A.Return):
            returns.append(
                _expression_annotation(
                    statement.value,
                    current,
                    owner_name,
                    function_returns,
                    method_returns,
                    class_names,
                    parents,
                )
            )
            continue

        if isinstance(statement, A.If):
            left_returns, left_environment = _analyze_statements(
                statement.then,
                dict(current),
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            right_returns, right_environment = _analyze_statements(
                statement.orelse or [],
                dict(current),
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            returns.extend(left_returns)
            returns.extend(right_returns)
            current = _merge_environments(left_environment, right_environment)
            continue

        if isinstance(statement, A.For):
            loop_environment = dict(current)
            if isinstance(statement.var, str):
                loop_environment[statement.var] = "any"
            for target in getattr(statement, "targets", []) or []:
                if isinstance(target, str):
                    loop_environment[target] = "any"
            loop_returns, loop_environment = _analyze_statements(
                statement.body,
                loop_environment,
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            returns.extend(loop_returns)
            current = _merge_environments(current, loop_environment)
            continue

        if isinstance(statement, A.While):
            loop_returns, loop_environment = _analyze_statements(
                statement.body,
                dict(current),
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            returns.extend(loop_returns)
            current = _merge_environments(current, loop_environment)
            continue

        if isinstance(statement, A.Try):
            branch_environments: list[dict] = []
            for body in [
                statement.body,
                statement.handler,
                statement.else_body,
                statement.finally_body,
            ]:
                branch_returns, branch_environment = _analyze_statements(
                    body or [],
                    dict(current),
                    owner_name,
                    function_returns,
                    method_returns,
                    class_names,
                    parents,
                )
                returns.extend(branch_returns)
                branch_environments.append(branch_environment)
            for _types, _binding, body in statement.extra_handlers:
                branch_returns, branch_environment = _analyze_statements(
                    body,
                    dict(current),
                    owner_name,
                    function_returns,
                    method_returns,
                    class_names,
                    parents,
                )
                returns.extend(branch_returns)
                branch_environments.append(branch_environment)
            for branch_environment in branch_environments:
                current = _merge_environments(current, branch_environment)

    return returns, current


def _infer_dynamic_returns(mod: A.Module) -> None:
    if getattr(mod, "_dynamic_returns_inferred", False):
        return
    mod._dynamic_returns_inferred = True

    class_names = {owner.name for owner in mod.classes}
    parents = _parent_table(mod)
    function_definitions = {function.name: function for function in mod.funcs}
    method_definitions = {
        (owner.name, method.name): method
        for owner in mod.classes
        for method in owner.methods
    }

    function_returns: dict[str, str] = {}
    method_returns: dict[tuple[str, str], str] = {}

    for name, definition in function_definitions.items():
        explicit = _annotation_name(definition.ret_type)
        if explicit is not None:
            function_returns[name] = explicit
    for key, definition in method_definitions.items():
        explicit = _annotation_name(definition.ret_type)
        if explicit is not None:
            method_returns[key] = explicit

    # A small fixed point resolves chains such as function -> constructor,
    # method -> function, and method -> same-named polymorphic method.
    for _iteration in range(12):
        changed = False

        for name, definition in function_definitions.items():
            if _annotation_name(definition.ret_type) is not None:
                continue
            environment = _seed_environment(definition, None, class_names)
            values, _environment = _analyze_statements(
                definition.body,
                environment,
                None,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            inferred = _merge_annotations(values)
            if inferred is not None and function_returns.get(name) != inferred:
                function_returns[name] = inferred
                changed = True

        for (owner_name, method_name), definition in method_definitions.items():
            if _annotation_name(definition.ret_type) is not None:
                continue
            environment = _seed_environment(definition, owner_name, class_names)
            values, _environment = _analyze_statements(
                definition.body,
                environment,
                owner_name,
                function_returns,
                method_returns,
                class_names,
                parents,
            )
            inferred = _merge_annotations(values)
            key = (owner_name, method_name)
            if inferred is not None and method_returns.get(key) != inferred:
                method_returns[key] = inferred
                changed = True

        if not changed:
            break

    for name, inferred in function_returns.items():
        definition = function_definitions[name]
        if definition.ret_type is None:
            definition.ret_type = (inferred, None)
    for key, inferred in method_returns.items():
        definition = method_definitions[key]
        if definition.ret_type is None:
            definition.ret_type = (inferred, None)


def _analyze_with_unreachable_project_tolerance(self: SemaAnalyzer) -> None:
    # Run the idempotent source-normalization passes before reachability and
    # return inference are calculated, so synthesized accessors participate.
    _normalize_method_receivers(self.mod)
    _lower_static_data_descriptors(self.mod)
    _infer_dynamic_returns(self.mod)

    reachable_functions, reachable_methods = _syntactic_reachable_names(self.mod)
    changed: list[tuple[object, bool]] = []

    for function in self.mod.funcs:
        if function.name in reachable_functions:
            continue
        original = bool(getattr(function, "is_stdlib", False))
        if not original:
            changed.append((function, original))
            function.is_stdlib = True

    for owner in self.mod.classes:
        for method in owner.methods:
            if (owner.name, method.name) in reachable_methods:
                continue
            original = bool(getattr(method, "is_stdlib", False))
            if not original:
                changed.append((method, original))
                method.is_stdlib = True

    try:
        _ORIGINAL_ANALYZE(self)
    finally:
        # `is_stdlib` is only borrowed to select the existing sema tolerance
        # path. Restore project identity before code generation and diagnostics.
        for definition, original in changed:
            definition.is_stdlib = original


if not getattr(SemaAnalyzer, "_asmpython_project_reachability_patch", False):
    SemaAnalyzer.analyze = _analyze_with_unreachable_project_tolerance
    SemaAnalyzer._asmpython_project_reachability_patch = True
