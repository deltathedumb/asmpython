"""Ensure lowered data descriptors win instance attribute lookup.

Static descriptor lowering creates shared descriptor objects plus synthesized
property getter/setter methods. Keeping a same-named class-variable field in the
native class signature makes ordinary instance access resolve to the descriptor
object itself, violating Python's data-descriptor precedence. Remove only the
synthetic class-variable shadow; the shared module binding remains available to
synthesized accessors and ``__set_name__`` initialization.
"""

from __future__ import annotations

from . import ast_nodes as A
from .language_compat_fixes import _lower_static_data_descriptors
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _remove_lowered_descriptor_shadows(mod: A.Module) -> None:
    if getattr(mod, "_descriptor_shadows_removed", False):
        return
    mod._descriptor_shadows_removed = True
    _lower_static_data_descriptors(mod)

    for owner in mod.classes:
        property_names = {
            method.name
            for method in owner.methods
            if "property" in (getattr(method, "decorators", []) or [])
        }
        if not property_names:
            continue
        retained = []
        for field_name, annotation, initializer in owner.class_vars:
            is_synthetic_descriptor = (
                field_name in property_names
                and isinstance(initializer, A.Name)
                and initializer.name.startswith("__asmpy_descriptor_")
            )
            if not is_synthetic_descriptor:
                retained.append((field_name, annotation, initializer))
        owner.class_vars = retained


def _analyze_with_descriptor_precedence(self: SemaAnalyzer) -> None:
    _remove_lowered_descriptor_shadows(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_descriptor_precedence_patch", False):
    SemaAnalyzer.analyze = _analyze_with_descriptor_precedence
    SemaAnalyzer._asmpython_descriptor_precedence_patch = True
