"""Lower class-qualified static/class method calls to direct functions.

The native backend's historical class-qualified call path passes an implicit
class slot even for ``staticmethod`` and does not preserve Python's concrete
``cls`` semantics for inherited ``classmethod`` calls. Whole-program source
already knows the receiver class, so materialize a normal module function and
rewrite the call before semantic analysis.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A
from . import class_value_compat_fixes as class_values
from . import concrete_specialization_compat_fixes as concrete
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _sanitize(value: str) -> str:
    return "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in value
    )


def _resolve_class_method(class_name: str, method_name: str, class_table: dict):
    current_name = class_name
    seen = set()
    while current_name and current_name not in seen:
        seen.add(current_name)
        owner = class_table.get(current_name)
        if owner is None:
            return None
        for method in owner.methods:
            if method.name == method_name:
                return owner, method
        current_name = owner.parent
    return None


def _prepare_clone(method, receiver_class: str, class_table: dict):
    cloned = class_values._clone(method)
    decorators = list(getattr(cloned, "decorators", []) or [])
    if "classmethod" in decorators:
        if not cloned.params:
            return None
        receiver = cloned.params[0]
        concrete._replace_identifier(cloned.body, receiver, receiver_class)
        cloned.body = concrete._fold_concrete_class_attributes(
            cloned.body, receiver_class, class_table
        )
        del cloned.params[0]
        if cloned.defaults:
            del cloned.defaults[0]
        if cloned.param_types:
            del cloned.param_types[0]
        cloned.readonly_params = [
            name for name in cloned.readonly_params if name != receiver
        ]
    cloned.decorators = []
    return cloned


def _lower_class_qualified_calls(mod: A.Module) -> None:
    if getattr(mod, "_class_qualified_calls_lowered", False):
        return
    mod._class_qualified_calls_lowered = True

    class_values._lower_finite_class_values(mod)
    class_tuples = class_values._static_class_tuples(mod)
    if class_tuples:
        concrete._specialize_materialized_classmethods(mod, class_tuples)

    class_table = {owner.name: owner for owner in mod.classes}
    generated = {}
    pending_functions = []

    def ensure_function(receiver_class: str, method_name: str):
        key = (receiver_class, method_name)
        existing = generated.get(key)
        if existing is not None:
            return existing
        resolved = _resolve_class_method(receiver_class, method_name, class_table)
        if resolved is None:
            return None
        _owner, method = resolved
        decorators = set(getattr(method, "decorators", []) or [])
        if not decorators.intersection({"staticmethod", "classmethod"}):
            return None
        cloned = _prepare_clone(method, receiver_class, class_table)
        if cloned is None:
            return None
        symbol = (
            "__asmpy_classcall_"
            + _sanitize(receiver_class)
            + "_"
            + _sanitize(method_name)
        )
        cloned.name = symbol
        generated[key] = symbol
        pending_functions.append(cloned)
        return symbol

    def rewrite(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, tuple):
            return tuple(rewrite(item) for item in value)
        if isinstance(value, dict):
            return {rewrite(key): rewrite(item) for key, item in value.items()}
        if isinstance(value, A.MethodCall):
            value.obj = rewrite(value.obj)
            value.args = [rewrite(argument) for argument in value.args]
            value.kwargs = [(name, rewrite(argument)) for name, argument in value.kwargs]
            if isinstance(value.obj, A.Name) and value.obj.name in class_table:
                symbol = ensure_function(value.obj.name, value.method)
                if symbol is not None:
                    return A.Call(
                        func=symbol,
                        args=value.args,
                        kwargs=value.kwargs,
                        pos=value.pos,
                    )
            return value
        if is_dataclass(value) and not isinstance(value, type):
            for data_field in fields(value):
                setattr(value, data_field.name, rewrite(getattr(value, data_field.name)))
        return value

    mod.body = rewrite(mod.body)
    for function in mod.funcs:
        function.body = rewrite(function.body)
    for owner in mod.classes:
        for method in owner.methods:
            method.body = rewrite(method.body)

    index = 0
    while index < len(pending_functions):
        function = pending_functions[index]
        index += 1
        function.body = rewrite(function.body)
        mod.funcs.append(function)


def _analyze_with_direct_class_calls(self: SemaAnalyzer) -> None:
    _lower_class_qualified_calls(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_direct_class_call_patch", False):
    SemaAnalyzer.analyze = _analyze_with_direct_class_calls
    SemaAnalyzer._asmpython_direct_class_call_patch = True
