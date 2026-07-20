"""Propagate collection field metadata through attribute reads and copies."""

from __future__ import annotations

from . import ast_nodes as A
from .dynamic_parameter_compat_fixes import _mark_dynamic_parameters
from .field_flow_compat_fixes import _annotate_object_fields
from .language_compat_fixes import _normalize_method_receivers
from .object_flow_compat_fixes import _walk_statements
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr


def _annotation_parts(annotation) -> tuple[str, "str | None"]:
    if isinstance(annotation, tuple) and annotation:
        base = annotation[0] if isinstance(annotation[0], str) else ""
        element = annotation[1] if len(annotation) > 1 and isinstance(annotation[1], str) else None
        return base, element
    if isinstance(annotation, str):
        return annotation, None
    return "", None


def _hierarchy_method_names(owner, classes: dict) -> set[str]:
    result: set[str] = set()
    current = owner
    seen: set[str] = set()
    while current is not None and current.name not in seen:
        seen.add(current.name)
        for method in current.methods:
            result.add(method.name)
        current = classes.get(current.parent)
    return result


def _recursive_object_element(owner, field_name: str, classes: dict) -> "str | None":
    methods = _hierarchy_method_names(owner, classes)
    for method in owner.methods:
        appended_parameters: set[str] = set()
        for node in _walk_statements(method.body):
            if (
                isinstance(node, A.MethodCall)
                and node.method == "append"
                and node.args
                and isinstance(node.obj, A.Attr)
                and isinstance(node.obj.obj, A.Name)
                and node.obj.obj.name == "self"
                and node.obj.name == field_name
                and isinstance(node.args[0], A.Name)
            ):
                appended_parameters.add(node.args[0].name)
        if not appended_parameters:
            continue
        for node in _walk_statements(method.body):
            if (
                isinstance(node, A.MethodCall)
                and isinstance(node.obj, A.Name)
                and node.obj.name in appended_parameters
                and node.method in methods
            ):
                return owner.name
    return None


def _cross_object_self_element(owner, field_name: str) -> "str | None":
    """Recognize ``other.field.append(self)`` on same-hierarchy relationships."""
    for method in owner.methods:
        parameters = set(method.params[1:])
        for node in _walk_statements(method.body):
            if not (
                isinstance(node, A.MethodCall)
                and node.method == "append"
                and len(node.args) == 1
                and isinstance(node.args[0], A.Name)
                and node.args[0].name == "self"
                and isinstance(node.obj, A.Attr)
                and node.obj.name == field_name
                and isinstance(node.obj.obj, A.Name)
                and node.obj.obj.name in parameters
            ):
                continue
            return owner.name
    return None


def _collection_fields(mod: A.Module) -> dict[tuple[str, str], tuple[str, str]]:
    result: dict[tuple[str, str], tuple[str, str]] = {}
    classes = {owner.name: owner for owner in mod.classes}
    for owner in mod.classes:
        for method in owner.methods:
            if method.name != "__init__":
                continue
            for statement in method.body:
                if not (
                    isinstance(statement, A.AttrAssign)
                    and isinstance(statement.obj, A.Name)
                    and statement.obj.name == "self"
                ):
                    continue
                base, element = _annotation_parts(statement.annot)
                if base not in ("list", "dict") or element is None:
                    continue
                if base == "list" and element == "any":
                    refined = _recursive_object_element(owner, statement.name, classes)
                    if refined is None:
                        refined = _cross_object_self_element(owner, statement.name)
                    if refined is not None:
                        element = refined
                        statement.annot = ("list", element)
                result[(owner.name, statement.name)] = (base, element)
    return result


def _lookup_field(self: SemaAnalyzer, receiver_type: str, field_name: str):
    table = getattr(self, "_compat_collection_fields", {})
    if not receiver_type.startswith("instance:"):
        return None
    classes = {owner.name: owner for owner in self.mod.classes}
    current = receiver_type.split(":", 1)[1]
    seen: set[str] = set()
    while current in classes and current not in seen:
        seen.add(current)
        value = table.get((current, field_name))
        if value is not None:
            return value
        current = classes[current].parent
    return None


def _check_expr_with_collection_fields(self: SemaAnalyzer, expression, scope) -> None:
    _ORIGINAL_CHECK_EXPR(self, expression, scope)

    if isinstance(expression, A.Attr):
        receiver_type = A.expr_type(expression.obj)
        metadata = _lookup_field(self, receiver_type, expression.name)
        if metadata is not None:
            base, element = metadata
            expression.inferred_type = base
            if base == "list":
                expression.list_el_type = element
            elif base == "dict":
                expression.value_type = element
        return

    if (
        isinstance(expression, A.Call)
        and expression.func in ("list", "tuple")
        and len(expression.args) == 1
    ):
        source = expression.args[0]
        element = getattr(source, "list_el_type", None)
        if isinstance(element, str):
            expression.list_el_type = element
            if expression.func == "tuple":
                expression.tuple_elem_types = []


def _analyze_with_collection_fields(self: SemaAnalyzer) -> None:
    _normalize_method_receivers(self.mod)
    _mark_dynamic_parameters(self.mod)
    _annotate_object_fields(self.mod)
    self._compat_collection_fields = _collection_fields(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_collection_field_check_patch", False):
    SemaAnalyzer._check_expr = _check_expr_with_collection_fields
    SemaAnalyzer._asmpython_collection_field_check_patch = True

if not getattr(SemaAnalyzer, "_asmpython_collection_field_analyze_patch", False):
    SemaAnalyzer.analyze = _analyze_with_collection_fields
    SemaAnalyzer._asmpython_collection_field_analyze_patch = True
