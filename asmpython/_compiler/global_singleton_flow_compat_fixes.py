"""Propagate method return types through module-global singleton objects.

Whole-program projects commonly create a registry or manager once at module
scope and delegate properties/methods through it::

    REGISTRY = Registry()

    @property
    def type_name(self):
        return REGISTRY.type_name(self)

The concrete class of ``REGISTRY`` is statically known. Resolve such delegated
returns before semantic analysis so native callers use the returned value's
actual representation rather than formatting a string pointer as an integer.
"""

from __future__ import annotations

from . import ast_nodes as A
from .container_field_compat_fixes import (
    _collection_fields,
    _refine_collection_method_returns,
)
from .dynamic_parameter_compat_fixes import _mark_dynamic_parameters
from .field_flow_compat_fixes import _annotate_object_fields
from .language_compat_fixes import _normalize_method_receivers
from .object_flow_compat_fixes import _walk_statements
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _annotation_name(annotation) -> "str | None":
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, tuple) and annotation:
        return annotation[0] if isinstance(annotation[0], str) else None
    return None


def _global_singletons(mod: A.Module) -> dict[str, str]:
    class_names = {owner.name for owner in mod.classes}
    result: dict[str, str] = {}
    for statement in mod.body:
        if (
            isinstance(statement, A.Assign)
            and isinstance(statement.target, str)
            and isinstance(statement.value, A.Call)
            and statement.value.func in class_names
        ):
            result[statement.target] = statement.value.func
    return result


def _resolve_method_return(
    class_name: str,
    method_name: str,
    classes: dict,
) -> "str | None":
    current = class_name
    seen: set[str] = set()
    while current in classes and current not in seen:
        seen.add(current)
        owner = classes[current]
        for method in owner.methods:
            if method.name != method_name:
                continue
            value = _annotation_name(method.ret_type)
            if value is not None:
                return value
        current = owner.parent
    return None


def _refine_global_singleton_returns(mod: A.Module) -> None:
    classes = {owner.name: owner for owner in mod.classes}
    globals_by_name = _global_singletons(mod)
    if not globals_by_name:
        return

    # Delegation chains can be several calls deep. A short fixed point mirrors
    # the compiler's ordinary unannotated-return inference pass.
    for _iteration in range(12):
        changed = False
        for owner in mod.classes:
            for method in owner.methods:
                observed: set[str] = set()
                for statement in _walk_statements(method.body):
                    if not isinstance(statement, A.Return):
                        continue
                    value = statement.value
                    if not (
                        isinstance(value, A.MethodCall)
                        and isinstance(value.obj, A.Name)
                        and value.obj.name in globals_by_name
                    ):
                        continue
                    return_type = _resolve_method_return(
                        globals_by_name[value.obj.name],
                        value.method,
                        classes,
                    )
                    if return_type not in (None, "any"):
                        observed.add(return_type)
                if len(observed) != 1:
                    continue
                inferred = next(iter(observed))
                current = _annotation_name(method.ret_type)
                if current != inferred:
                    method.ret_type = (inferred, None)
                    changed = True
        if not changed:
            break


def _analyze_with_global_singleton_flow(self: SemaAnalyzer) -> None:
    # Re-run the idempotent field passes here so their concrete collection
    # metadata is available before resolving singleton delegation.
    _normalize_method_receivers(self.mod)
    _mark_dynamic_parameters(self.mod)
    _annotate_object_fields(self.mod)
    table = _collection_fields(self.mod)
    _refine_collection_method_returns(self.mod, table)
    _refine_global_singleton_returns(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_global_singleton_flow_patch", False):
    SemaAnalyzer.analyze = _analyze_with_global_singleton_flow
    SemaAnalyzer._asmpython_global_singleton_flow_patch = True
