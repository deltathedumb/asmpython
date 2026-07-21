"""Whole-program specialization for concrete class and argument values.

Finite class lowering may materialize an inherited classmethod on a concrete
subclass so native codegen has a ``Subclass__method`` symbol. Python still
requires ``cls`` inside that body to be the concrete subclass. This pass turns
those copied methods into static, subclass-specialized bodies.

It also annotates an otherwise-unannotated top-level parameter when every call
site supplies the same statically-known value kind. This keeps ordinary dynamic
Python source while giving native method dispatch the concrete representation it
needs (for example a string parameter used with ``startswith``).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A
from . import class_value_compat_fixes as class_values
from .metaclass_compat_fixes import _walk_stmts
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _replace_identifier(value, old: str, new: str) -> None:
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    if isinstance(value, A.Name):
        if value.name == old:
            value.name = new
        return
    if isinstance(value, A.Call) and value.func == old:
        value.func = new
    if isinstance(value, list) or isinstance(value, tuple):
        for item in value:
            _replace_identifier(item, old, new)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _replace_identifier(key, old, new)
            _replace_identifier(item, old, new)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for data_field in fields(value):
            _replace_identifier(getattr(value, data_field.name), old, new)


def _nearest_parent_method(owner, method_name: str, class_table: dict):
    parent_name = owner.parent
    seen = set()
    while parent_name and parent_name not in seen:
        seen.add(parent_name)
        parent = class_table.get(parent_name)
        if parent is None:
            return None
        for method in parent.methods:
            if method.name == method_name:
                return method
        parent_name = parent.parent
    return None


def _specialize_materialized_classmethods(mod: A.Module, class_tuples: dict) -> None:
    class_table = {owner.name: owner for owner in mod.classes}
    concrete_names = {
        class_name
        for entries in class_tuples.values()
        for class_name in entries
    }
    for class_name in concrete_names:
        owner = class_table.get(class_name)
        if owner is None:
            continue
        for method in owner.methods:
            decorators = list(getattr(method, "decorators", []) or [])
            if "classmethod" not in decorators or not method.params:
                continue
            inherited = _nearest_parent_method(owner, method.name, class_table)
            if inherited is None:
                continue
            inherited_decorators = list(getattr(inherited, "decorators", []) or [])
            # The finite-class pass copies the nearest method verbatim, preserving
            # source position. Do not alter an explicit subclass override.
            if "classmethod" not in inherited_decorators or method.pos != inherited.pos:
                continue
            receiver = method.params[0]
            _replace_identifier(method.body, receiver, class_name)
            del method.params[0]
            if method.defaults:
                del method.defaults[0]
            if method.param_types:
                del method.param_types[0]
            method.readonly_params = [
                name for name in method.readonly_params if name != receiver
            ]
            method.decorators = [
                decorator
                for decorator in decorators
                if decorator != "classmethod"
            ]
            if "staticmethod" not in method.decorators:
                method.decorators.append("staticmethod")


def _literal_annotation(expression, class_names: set):
    if isinstance(expression, A.StrLit):
        return ("str", None)
    if isinstance(expression, A.FloatLit):
        return ("float", None)
    if isinstance(expression, A.IntLit):
        if getattr(expression, "is_none", False):
            return None
        return ("bool" if getattr(expression, "is_bool", False) else "int", None)
    if isinstance(expression, A.ListLit):
        return ("list", getattr(expression, "el_type", None))
    if isinstance(expression, A.DictLit):
        return ("dict", None)
    if isinstance(expression, A.TupleLit):
        return ("tuple", None)
    if isinstance(expression, A.SetLit):
        return ("set", None)
    if isinstance(expression, A.Call) and expression.func in class_names:
        return (expression.func, None)
    if isinstance(expression, A.Name) and expression.name in class_names:
        return ("type", None)
    return None


def _all_calls(mod: A.Module) -> list:
    calls = []
    statement_lists = [mod.body]
    statement_lists.extend(function.body for function in mod.funcs)
    for owner in mod.classes:
        statement_lists.extend(method.body for method in owner.methods)
    for statements in statement_lists:
        for node in _walk_stmts(statements):
            if isinstance(node, A.Call):
                calls.append(node)
    return calls


def _bound_argument(call: A.Call, function, parameter_index: int):
    if parameter_index < len(call.args):
        return call.args[parameter_index]
    parameter = function.params[parameter_index]
    for name, value in call.kwargs:
        if name == parameter:
            return value
    if parameter_index < len(function.defaults):
        return function.defaults[parameter_index]
    return None


def _specialize_unanimous_function_parameters(mod: A.Module) -> None:
    class_names = {owner.name for owner in mod.classes}
    definitions = {}
    duplicates = set()
    for function in mod.funcs:
        if function.name in definitions:
            duplicates.add(function.name)
        else:
            definitions[function.name] = function
    for name in duplicates:
        definitions.pop(name, None)

    calls_by_name = {}
    for call in _all_calls(mod):
        calls_by_name.setdefault(call.func, []).append(call)

    for name, function in definitions.items():
        calls = calls_by_name.get(name, [])
        if not calls:
            continue
        param_types = list(function.param_types)
        while len(param_types) < len(function.params):
            param_types.append(None)
        changed = False
        for parameter_index in range(len(function.params)):
            if param_types[parameter_index] is not None:
                continue
            observed = []
            complete = True
            for call in calls:
                argument = _bound_argument(call, function, parameter_index)
                annotation = _literal_annotation(argument, class_names)
                if annotation is None:
                    complete = False
                    break
                observed.append(annotation)
            if complete and observed and all(value == observed[0] for value in observed):
                param_types[parameter_index] = observed[0]
                changed = True
        if changed:
            function.param_types = param_types


def _analyze_with_concrete_specializations(self: SemaAnalyzer) -> None:
    class_values._lower_finite_class_values(self.mod)
    class_tuples = class_values._static_class_tuples(self.mod)
    if class_tuples:
        _specialize_materialized_classmethods(self.mod, class_tuples)
    _specialize_unanimous_function_parameters(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_concrete_specialization_patch", False):
    SemaAnalyzer.analyze = _analyze_with_concrete_specializations
    SemaAnalyzer._asmpython_concrete_specialization_patch = True
