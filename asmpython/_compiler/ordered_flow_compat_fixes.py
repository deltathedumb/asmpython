"""Guarantee dependency-ordered whole-program type inference.

Several compatibility passes wrap ``SemaAnalyzer.analyze``. This outermost
coordinator runs their idempotent transformations in semantic dependency order
before any wrapper can request field metadata prematurely, then performs a
specific fixed-point return pass and constructor/field annotation.
"""

from __future__ import annotations

from . import ast_nodes as A
from .analysis_compat_fixes import _infer_dynamic_returns
from .dynamic_parameter_compat_fixes import _mark_dynamic_parameters
from .field_flow_compat_fixes import (
    _annotate_object_fields,
    _field_tables,
    _literal_type,
    _parent_map,
    _return_tables,
)
from .language_compat_fixes import (
    _lower_static_data_descriptors,
    _normalize_method_receivers,
)
from .sema import SemaAnalyzer
from .type_parameter_compat_fixes import _lower_type_parameter_specializations


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_UNKNOWN = "<unknown>"
_NONE = "<none>"


def _annotation_name(annotation) -> "str | None":
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, tuple) and annotation:
        return annotation[0] if isinstance(annotation[0], str) else None
    return None


def _seed_environment(definition, owner_name: "str | None") -> dict[str, str]:
    environment: dict[str, str] = {}
    decorators = list(getattr(definition, "decorators", []) or [])
    for index, parameter in enumerate(definition.params):
        if owner_name is not None and index == 0 and "staticmethod" not in decorators:
            environment[parameter] = "any" if "classmethod" in decorators else owner_name
            continue
        annotation = (
            definition.param_types[index]
            if index < len(definition.param_types)
            else None
        )
        value = _annotation_name(annotation)
        if value is not None:
            environment[parameter] = value
            continue
        default = definition.defaults[index] if index < len(definition.defaults) else None
        if isinstance(default, A.StrLit):
            environment[parameter] = "str"
        elif isinstance(default, A.FloatLit):
            environment[parameter] = "float"
        elif isinstance(default, A.IntLit) and not getattr(default, "is_none", False):
            environment[parameter] = "bool" if getattr(default, "is_bool", False) else "int"
        elif isinstance(default, A.ListLit):
            environment[parameter] = "list"
        elif isinstance(default, A.DictLit):
            environment[parameter] = "dict"
        else:
            environment[parameter] = "any"
    return environment


def _merge_types(values: list[str]) -> "str | None":
    concrete = [value for value in values if value not in (_UNKNOWN, _NONE)]
    if not concrete:
        return None
    unique = set(concrete)
    if len(unique) == 1:
        return concrete[0]
    if unique.issubset({"bool", "int", "float"}):
        return "float" if "float" in unique else "int"
    specific = [value for value in concrete if value != "any"]
    if specific and len(set(specific)) == 1:
        return specific[0]
    return "any"


def _body_return_types(
    statements: list,
    environment: dict,
    owner_name: "str | None",
    class_names: set,
    function_returns: dict,
    method_returns: dict,
    field_types: dict,
    parents: dict,
) -> list[str]:
    result: list[str] = []
    current = dict(environment)

    for statement in statements:
        if isinstance(statement, A.Assign) and isinstance(statement.target, str):
            current[statement.target] = _literal_type(
                statement.value,
                current,
                class_names,
                function_returns,
                method_returns,
                field_types,
                parents,
            )
            continue
        if isinstance(statement, A.MultiAssign):
            value = _literal_type(
                statement.value,
                current,
                class_names,
                function_returns,
                method_returns,
                field_types,
                parents,
            )
            for target in statement.targets:
                if isinstance(target, str):
                    current[target] = value
            continue
        if isinstance(statement, A.TupleAssign):
            for target in statement.targets:
                if isinstance(target, A.Name):
                    current[target.name] = "any"
                elif isinstance(target, A.StarTarget):
                    current[target.name] = "list"
            continue
        if isinstance(statement, A.Return):
            result.append(
                _literal_type(
                    statement.value,
                    current,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            )
            continue
        if isinstance(statement, A.If):
            result.extend(
                _body_return_types(
                    statement.then,
                    dict(current),
                    owner_name,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            )
            result.extend(
                _body_return_types(
                    statement.orelse or [],
                    dict(current),
                    owner_name,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            )
            continue
        if isinstance(statement, (A.For, A.While)):
            result.extend(
                _body_return_types(
                    statement.body,
                    dict(current),
                    owner_name,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            )
            result.extend(
                _body_return_types(
                    statement.orelse or [],
                    dict(current),
                    owner_name,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            )
            continue
        if isinstance(statement, A.Try):
            for body in (
                statement.body,
                statement.handler or [],
                statement.else_body or [],
                statement.finally_body or [],
            ):
                result.extend(
                    _body_return_types(
                        body,
                        dict(current),
                        owner_name,
                        class_names,
                        function_returns,
                        method_returns,
                        field_types,
                        parents,
                    )
                )
            for _types, _binding, body in statement.extra_handlers:
                result.extend(
                    _body_return_types(
                        body,
                        dict(current),
                        owner_name,
                        class_names,
                        function_returns,
                        method_returns,
                        field_types,
                        parents,
                    )
                )
    return result


def _infer_specific_returns(mod: A.Module) -> None:
    class_names = {owner.name for owner in mod.classes}
    parents = _parent_map(mod)

    for _iteration in range(12):
        changed = False
        function_returns, method_returns = _return_tables(mod)
        field_types, _field_elements = _field_tables(mod)

        for function in mod.funcs:
            environment = _seed_environment(function, None)
            inferred = _merge_types(
                _body_return_types(
                    function.body,
                    environment,
                    None,
                    class_names,
                    function_returns,
                    method_returns,
                    field_types,
                    parents,
                )
            )
            current = _annotation_name(function.ret_type)
            if (
                current not in ("tuple", "list", "dict", "set")
                and inferred is not None
                and inferred not in (_UNKNOWN, "any")
                and current != inferred
            ):
                function.ret_type = (inferred, None)
                changed = True

        for owner in mod.classes:
            for method in owner.methods:
                environment = _seed_environment(method, owner.name)
                inferred = _merge_types(
                    _body_return_types(
                        method.body,
                        environment,
                        owner.name,
                        class_names,
                        function_returns,
                        method_returns,
                        field_types,
                        parents,
                    )
                )
                current = _annotation_name(method.ret_type)
                if (
                    current not in ("tuple", "list", "dict", "set")
                    and inferred is not None
                    and inferred not in (_UNKNOWN, "any")
                    and current != inferred
                ):
                    method.ret_type = (inferred, None)
                    changed = True

        if not changed:
            break


def _analyze_with_ordered_flow(self: SemaAnalyzer) -> None:
    _normalize_method_receivers(self.mod)
    _lower_static_data_descriptors(self.mod)
    _lower_type_parameter_specializations(self.mod)
    _mark_dynamic_parameters(self.mod)
    # Infer unannotated parameter types from call sites, and stamp a FLOAT
    # inference onto the FuncDef's `param_types`, BEFORE whole-program return
    # inference runs below. Return inference reads parameter types to type a
    # `return a + b` / `return x` body, and FuncSig construction (in the
    # original analyze that runs last) reads the same `param_types` -- so a
    # float parameter learned only from call sites has to be in place first,
    # or `def add(a, b): return a + b` called `add(1.5, 2.5)` freezes its
    # return type at `any` here (param still untyped) and later marshals the
    # float arguments through GP registers, reinterpreting their bits as ints.
    # `_infer_unannotated_params` still runs again inside the original analyze
    # (idempotent: these params are now annotated, so it re-confirms the rest).
    self._infer_unannotated_params()
    self._apply_inferred_float_params()
    _infer_dynamic_returns(self.mod)
    _infer_specific_returns(self.mod)

    # An older outer wrapper may have requested field metadata before the
    # dependency chain above was complete. Rebuild it once from the normalized
    # AST; the operation only mutates annotations, not source behavior.
    self.mod._object_fields_annotated = False
    _annotate_object_fields(self.mod)
    _infer_specific_returns(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_ordered_flow_patch", False):
    SemaAnalyzer.analyze = _analyze_with_ordered_flow
    SemaAnalyzer._asmpython_ordered_flow_patch = True
