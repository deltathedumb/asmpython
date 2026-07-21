"""Lower finite class-valued collections before semantic analysis.

Native whole-program compilation can statically resolve class objects stored
in literal tuples. This pass folds literal tuple indexing and unrolls safe
loops over those tuples, replacing the loop variable with each concrete
class. It preserves ordinary Python source while avoiding a general dynamic
metatype runtime for cases whose complete class set is already known.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


class _Splice:
    def __init__(self, items: list) -> None:
        self.items = items


def _clone(value):
    if isinstance(value, list):
        return [_clone(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_clone(item) for item in value)
    if isinstance(value, dict):
        return {_clone(key): _clone(item) for key, item in value.items()}
    if isinstance(value, set):
        return {_clone(item) for item in value}
    if is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        init_values = {}
        deferred = []
        for data_field in fields(value):
            cloned_value = _clone(getattr(value, data_field.name))
            if data_field.init:
                init_values[data_field.name] = cloned_value
            else:
                deferred.append((data_field.name, cloned_value))
        cloned = cls(**init_values)
        for name, cloned_value in deferred:
            setattr(cloned, name, cloned_value)
        return cloned
    return value


def _static_class_tuples(mod: A.Module) -> dict:
    class_names = {definition.name for definition in mod.classes}
    result = {}
    for statement in mod.body:
        if not isinstance(statement, A.Assign):
            continue
        if not isinstance(statement.value, A.TupleLit):
            continue
        names = []
        valid = True
        for element in statement.value.elems:
            if not isinstance(element, A.Name) or element.name not in class_names:
                valid = False
                break
            names.append(element.name)
        if valid and names:
            result[statement.target] = tuple(names)
    return result


def _contains_loop_control(value) -> bool:
    if isinstance(value, (A.Break, A.Continue)):
        return True
    if isinstance(value, (str, int, float, bool, type(None))):
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_contains_loop_control(item) for item in value)
    if isinstance(value, dict):
        return any(
            _contains_loop_control(key) or _contains_loop_control(item)
            for key, item in value.items()
        )
    if is_dataclass(value) and not isinstance(value, type):
        return any(
            _contains_loop_control(getattr(value, data_field.name))
            for data_field in fields(value)
        )
    return False


def _binds_name(value, name: str) -> bool:
    if isinstance(value, A.Assign) and value.target == name:
        return True
    if isinstance(value, A.MultiAssign) and name in value.targets:
        return True
    if isinstance(value, A.For):
        if value.var == name or name in value.targets:
            return True
    if isinstance(value, (str, int, float, bool, type(None))):
        return False
    if isinstance(value, (list, tuple, set)):
        return any(_binds_name(item, name) for item in value)
    if isinstance(value, dict):
        return any(
            _binds_name(key, name) or _binds_name(item, name)
            for key, item in value.items()
        )
    if is_dataclass(value) and not isinstance(value, type):
        return any(
            _binds_name(getattr(value, data_field.name), name)
            for data_field in fields(value)
        )
    return False


def _rewrite(value, class_tuples: dict, substitutions: dict):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        rewritten = []
        for item in value:
            transformed = _rewrite(item, class_tuples, substitutions)
            if isinstance(transformed, _Splice):
                rewritten.extend(transformed.items)
            else:
                rewritten.append(transformed)
        return rewritten
    if isinstance(value, tuple):
        return tuple(_rewrite(item, class_tuples, substitutions) for item in value)
    if isinstance(value, dict):
        return {
            _rewrite(key, class_tuples, substitutions): _rewrite(
                item, class_tuples, substitutions
            )
            for key, item in value.items()
        }
    if isinstance(value, set):
        return {_rewrite(item, class_tuples, substitutions) for item in value}

    if isinstance(value, A.Name) and value.name in substitutions:
        return A.Name(name=substitutions[value.name], pos=value.pos)

    if isinstance(value, A.Call) and value.func in substitutions:
        value.func = substitutions[value.func]

    if isinstance(value, A.Subscript):
        value.obj = _rewrite(value.obj, class_tuples, substitutions)
        value.index = _rewrite(value.index, class_tuples, substitutions)
        if (
            isinstance(value.obj, A.Name)
            and value.obj.name in class_tuples
            and isinstance(value.index, A.IntLit)
        ):
            entries = class_tuples[value.obj.name]
            index = value.index.value
            if index < 0:
                index += len(entries)
            if 0 <= index < len(entries):
                return A.Name(name=entries[index], pos=value.pos)
        return value

    if isinstance(value, A.For):
        value.iter = _rewrite(value.iter, class_tuples, substitutions)
        if (
            isinstance(value.iter, A.Name)
            and value.iter.name in class_tuples
            and not value.targets
            and not value.orelse
            and not _contains_loop_control(value.body)
            and not _binds_name(value.body, value.var)
        ):
            expanded = []
            for class_name in class_tuples[value.iter.name]:
                body = _clone(value.body)
                local_substitutions = dict(substitutions)
                local_substitutions[value.var] = class_name
                transformed = _rewrite(body, class_tuples, local_substitutions)
                expanded.extend(transformed)
            return _Splice(expanded)

    if is_dataclass(value) and not isinstance(value, type):
        for data_field in fields(value):
            current = getattr(value, data_field.name)
            transformed = _rewrite(current, class_tuples, substitutions)
            if isinstance(transformed, _Splice):
                raise TypeError(
                    "class-value loop expansion requires a statement list"
                )
            setattr(value, data_field.name, transformed)
    return value


def _lower_finite_class_values(mod: A.Module) -> None:
    if getattr(mod, "_finite_class_values_lowered", False):
        return
    mod._finite_class_values_lowered = True
    class_tuples = _static_class_tuples(mod)
    if not class_tuples:
        return
    mod.body = _rewrite(mod.body, class_tuples, {})
    for function in mod.funcs:
        function.body = _rewrite(function.body, class_tuples, {})
    for owner in mod.classes:
        for method in owner.methods:
            method.body = _rewrite(method.body, class_tuples, {})


def _analyze_with_finite_class_values(self: SemaAnalyzer) -> None:
    _lower_finite_class_values(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_finite_class_value_patch", False):
    SemaAnalyzer.analyze = _analyze_with_finite_class_values
    SemaAnalyzer._asmpython_finite_class_value_patch = True
