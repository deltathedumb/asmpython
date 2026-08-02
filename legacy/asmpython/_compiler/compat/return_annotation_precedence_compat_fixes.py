"""Keep authoritative return annotations above inferred implementation types.

Explicit source annotations and compiler-synthesized property return types are
contracts. Whole-program return inference may fill missing annotations, but it
must never replace those contracts with an implementation-detail type from a
callee body (for example ``Property.__get__`` returning the descriptor on class
access while an instance property exposes ``Vec3``).
"""

from __future__ import annotations

from .descriptor_precedence_compat_fixes import _remove_lowered_descriptor_shadows
from . import ordered_flow_compat_fixes as ordered_flow
from ..sema import SemaAnalyzer


_ORIGINAL_INIT = SemaAnalyzer.__init__
_ORIGINAL_INFER_SPECIFIC_RETURNS = ordered_flow._infer_specific_returns


def _definitions(mod):
    for function in mod.funcs:
        yield function
    for owner in mod.classes:
        for method in owner.methods:
            yield method


def _mark_authoritative_returns(mod) -> None:
    if getattr(mod, "_authoritative_returns_marked", False):
        return
    mod._authoritative_returns_marked = True

    # Descriptor lowering synthesizes property methods with declared exposed
    # types, so it must run before the authoritative snapshot is taken.
    _remove_lowered_descriptor_shadows(mod)
    for definition in _definitions(mod):
        if definition.ret_type is not None:
            definition._authoritative_ret_type = definition.ret_type


def _init_with_return_annotation_precedence(self, mod, *args, **kwargs) -> None:
    _mark_authoritative_returns(mod)
    _ORIGINAL_INIT(self, mod, *args, **kwargs)


def _infer_specific_returns_without_overrides(mod) -> None:
    authoritative = {
        id(definition): getattr(definition, "_authoritative_ret_type")
        for definition in _definitions(mod)
        if hasattr(definition, "_authoritative_ret_type")
    }
    _ORIGINAL_INFER_SPECIFIC_RETURNS(mod)
    for definition in _definitions(mod):
        saved = authoritative.get(id(definition))
        if saved is not None:
            definition.ret_type = saved


ordered_flow._infer_specific_returns = _infer_specific_returns_without_overrides

if not getattr(SemaAnalyzer, "_asmpython_return_annotation_init_patch", False):
    SemaAnalyzer.__init__ = _init_with_return_annotation_precedence
    SemaAnalyzer._asmpython_return_annotation_init_patch = True
