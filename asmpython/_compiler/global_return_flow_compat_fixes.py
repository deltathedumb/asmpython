"""Use module-global value types during fixed-point return inference.

Python functions and properties resolve module globals at call time.  The
whole-program return pass previously seeded only parameters, so a property such
as ``return REGISTRY.type_name(self)`` could not identify ``REGISTRY``'s class.
When unrelated classes exposed the same method name with different return
shapes, inference fell back to the historical ``int`` default.

This pass derives stable module-global types from top-level assignments and
makes them visible while the existing ordered fixed-point return pass runs.
"""

from __future__ import annotations

from . import ast_nodes as A
from . import ordered_flow_compat_fixes as ordered_flow
from .field_flow_compat_fixes import (
    _UNKNOWN,
    _NONE,
    _field_tables,
    _literal_type,
    _parent_map,
    _return_tables,
)


_ORIGINAL_INFER_SPECIFIC_RETURNS = ordered_flow._infer_specific_returns
_ORIGINAL_SEED_ENVIRONMENT = ordered_flow._seed_environment


def _module_global_types(mod: A.Module) -> dict[str, str]:
    class_names = {owner.name for owner in mod.classes}
    parents = _parent_map(mod)
    environment: dict[str, str] = {}

    # Iterate because one top-level assignment may reference an earlier global
    # whose class/method return becomes known on a later fixed-point pass.
    for _iteration in range(8):
        changed = False
        function_returns, method_returns = _return_tables(mod)
        field_types, _field_elements = _field_tables(mod)
        for statement in mod.body:
            if not isinstance(statement, A.Assign) or not isinstance(statement.target, str):
                continue
            inferred = _literal_type(
                statement.value,
                environment,
                class_names,
                function_returns,
                method_returns,
                field_types,
                parents,
            )
            if inferred in (_UNKNOWN, _NONE, "any"):
                continue
            if environment.get(statement.target) != inferred:
                environment[statement.target] = inferred
                changed = True
        if not changed:
            break
    return environment


def _infer_specific_returns_with_globals(mod: A.Module) -> None:
    globals_environment = _module_global_types(mod)
    if not globals_environment:
        _ORIGINAL_INFER_SPECIFIC_RETURNS(mod)
        return

    def seed_with_globals(definition, owner_name):
        environment = dict(globals_environment)
        environment.update(_ORIGINAL_SEED_ENVIRONMENT(definition, owner_name))
        return environment

    previous_seed = ordered_flow._seed_environment
    ordered_flow._seed_environment = seed_with_globals
    try:
        _ORIGINAL_INFER_SPECIFIC_RETURNS(mod)
    finally:
        ordered_flow._seed_environment = previous_seed


if not getattr(ordered_flow, "_asmpython_global_return_flow_patch", False):
    ordered_flow._infer_specific_returns = _infer_specific_returns_with_globals
    ordered_flow._asmpython_global_return_flow_patch = True
