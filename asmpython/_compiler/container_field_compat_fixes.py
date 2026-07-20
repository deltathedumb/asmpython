"""Propagate collection field metadata through attribute reads and copies."""

from __future__ import annotations

from . import ast_nodes as A
from .dynamic_parameter_compat_fixes import _mark_dynamic_parameters
from .field_flow_compat_fixes import _annotate_object_fields
from .language_compat_fixes import _normalize_method_receivers
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


def _collection_fields(mod: A.Module) -> dict[tuple[str, str], tuple[str, str]]:
    result: dict[tuple[str, str], tuple[str, str]] = {}
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
                if base in ("list", "dict") and element is not None:
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
