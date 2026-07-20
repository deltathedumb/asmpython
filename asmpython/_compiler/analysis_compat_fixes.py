"""Compatibility fixes for semantic analysis of whole-program projects.

The whole-program loader merges every reachable module's definitions into one
module, including helpers which the entry point never calls. asmpython already
suppresses semantic failures in unreachable bundled-stdlib bodies; applying the
same conservative reachability rule to ordinary project modules prevents an
unused optional backend from blocking an otherwise native-compilable program.
Reachable functions and methods remain fully checked.
"""

from __future__ import annotations

from .language_compat_fixes import (
    _lower_static_data_descriptors,
    _normalize_method_receivers,
)
from .sema import SemaAnalyzer, _syntactic_reachable_names


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _analyze_with_unreachable_project_tolerance(self: SemaAnalyzer) -> None:
    # Run the idempotent source-normalization passes before reachability is
    # calculated, so synthesized descriptor accessors participate normally.
    _normalize_method_receivers(self.mod)
    _lower_static_data_descriptors(self.mod)

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
