"""Whole-program specialization for finite type-valued parameters.

A common Python pattern accepts a class and later uses it in ``isinstance`` or
constructor position::

    def ensure(self, service_type):
        found = self.find(service_type)
        return found or service_type()

The whole-program compiler can see calls such as ``ensure(World)``. This pass
clones the method for each concrete class argument, removes the type parameter,
replaces its uses with the class literal, and rewrites the call site. Recursive
calls into other type-parameter methods are specialized to a fixed point.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from .. import ast_nodes as A
from .metaclass_compat_fixes import _walk_expr, _walk_stmts
from ..sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze


def _clone(value):
    """Clone parser AST dataclasses without passing non-init fields to __init__."""
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
        init_values: dict = {}
        deferred: list = []
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


def _replace_parameter(value, parameter: str, class_name: str) -> None:
    if isinstance(value, A.Name):
        if value.name == parameter:
            value.name = class_name
        return
    if isinstance(value, A.Call) and value.func == parameter:
        value.func = class_name
    if isinstance(value, list):
        for item in value:
            _replace_parameter(item, parameter, class_name)
        return
    if isinstance(value, tuple):
        for item in value:
            _replace_parameter(item, parameter, class_name)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _replace_parameter(key, parameter, class_name)
            _replace_parameter(item, parameter, class_name)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for data_field in fields(value):
            _replace_parameter(getattr(value, data_field.name), parameter, class_name)


def _expression_uses_type_parameter(expr, parameter: str) -> bool:
    for node in _walk_expr(expr):
        if isinstance(node, A.Call) and node.func == parameter:
            return True
        if (
            isinstance(node, A.Call)
            and node.func == "isinstance"
            and len(node.args) == 2
            and isinstance(node.args[1], A.Name)
            and node.args[1].name == parameter
        ):
            return True
    return False


def _type_parameter_indices(func, is_method: bool) -> list:
    result: list = []
    start = (
        1
        if is_method and "staticmethod" not in getattr(func, "decorators", [])
        else 0
    )
    for index in range(start, len(func.params)):
        parameter = func.params[index]
        found = False
        for node in _walk_stmts(func.body):
            if isinstance(node, A.Call) and node.func == parameter:
                found = True
                break
            if (
                isinstance(node, A.Call)
                and node.func == "isinstance"
                and len(node.args) == 2
                and isinstance(node.args[1], A.Name)
                and node.args[1].name == parameter
            ):
                found = True
                break
        if found:
            result.append(index)
    return result


def _assignment_related_variables(func, parameter: str) -> set:
    related = set()
    changed = True
    while changed:
        changed = False
        for node in _walk_stmts(func.body):
            if not isinstance(node, A.Assign):
                continue
            value = node.value
            uses_parameter = _expression_uses_type_parameter(value, parameter)
            uses_related = any(
                isinstance(expr, A.Name) and expr.name in related
                for expr in _walk_expr(value)
            )
            passes_parameter = any(
                isinstance(expr, A.Name) and expr.name == parameter
                for expr in _walk_expr(value)
            ) and isinstance(value, (A.Call, A.MethodCall))
            if (
                uses_parameter or uses_related or passes_parameter
            ) and node.target not in related:
                related.add(node.target)
                changed = True
    return related


def _guarded_type_variable(test, parameter: str) -> "str | None":
    for expr in _walk_expr(test):
        if (
            isinstance(expr, A.Call)
            and expr.func == "isinstance"
            and len(expr.args) == 2
            and isinstance(expr.args[0], A.Name)
            and isinstance(expr.args[1], A.Name)
            and expr.args[1].name == parameter
        ):
            return expr.args[0].name
    return None


def _returns_specialized_type(func, parameter: str) -> bool:
    related = _assignment_related_variables(func, parameter)
    saw_value = False
    valid = True

    def visit(stmts: list, guarded: set) -> None:
        nonlocal saw_value, valid
        for stmt in stmts:
            if isinstance(stmt, A.Return):
                value = stmt.value
                if value is None or (
                    isinstance(value, A.IntLit) and getattr(value, "is_none", False)
                ):
                    continue
                saw_value = True
                if isinstance(value, A.Call) and value.func == parameter:
                    continue
                if isinstance(value, A.Name) and (
                    value.name in related or value.name in guarded
                ):
                    continue
                valid = False
                continue
            if isinstance(stmt, A.If):
                narrowed = _guarded_type_variable(stmt.test, parameter)
                then_guarded = set(guarded)
                if narrowed is not None:
                    then_guarded.add(narrowed)
                visit(stmt.then, then_guarded)
                visit(stmt.orelse, guarded)
                continue
            for attr in ("body", "handler", "else_body", "finally_body"):
                nested = getattr(stmt, attr, None)
                if isinstance(nested, list):
                    visit(nested, guarded)

    visit(func.body, set())
    return saw_value and valid


def _all_statement_lists(mod: A.Module) -> list:
    result = [mod.body]
    for func in mod.funcs:
        result.append(func.body)
    for cls in mod.classes:
        for method in cls.methods:
            result.append(method.body)
    return result


def _all_call_nodes(mod: A.Module) -> list:
    calls: list = []
    for stmts in _all_statement_lists(mod):
        for node in _walk_stmts(stmts):
            if isinstance(node, (A.Call, A.MethodCall)):
                calls.append(node)
    return calls


def _call_sites_with_owners(mod: A.Module):
    """Yield ``(owning FuncDef id or None, call)`` for the complete module."""
    for node in _walk_stmts(mod.body):
        if isinstance(node, (A.Call, A.MethodCall)):
            yield (None, node)
    for func in mod.funcs:
        owner_id = id(func)
        for node in _walk_stmts(func.body):
            if isinstance(node, (A.Call, A.MethodCall)):
                yield (owner_id, node)
    for cls in mod.classes:
        for method in cls.methods:
            owner_id = id(method)
            for node in _walk_stmts(method.body):
                if isinstance(node, (A.Call, A.MethodCall)):
                    yield (owner_id, node)


def _argument_binding(call, func, parameter_index: int, is_method: bool):
    parameter = func.params[parameter_index]
    offset = 0
    if is_method and "staticmethod" not in getattr(func, "decorators", []):
        offset = 1
    positional_index = parameter_index - offset
    if 0 <= positional_index < len(call.args):
        return call.args[positional_index], ("positional", positional_index)
    for index, (name, value) in enumerate(call.kwargs):
        if name == parameter:
            return value, ("keyword", index)
    return None, None


def _remove_bound_argument(call, binding) -> None:
    kind, index = binding
    if kind == "positional":
        del call.args[index]
    else:
        del call.kwargs[index]


def _sanitize(name: str) -> str:
    out = []
    for char in name:
        out.append(char if char.isalnum() or char == "_" else "_")
    return "".join(out)


def _specialized_clone(func, parameter_index: int, class_name: str, clone_name: str):
    cloned = _clone(func)
    parameter = cloned.params[parameter_index]
    cloned.name = clone_name
    del cloned.params[parameter_index]
    if parameter_index < len(cloned.defaults):
        del cloned.defaults[parameter_index]
    if parameter_index < len(cloned.param_types):
        del cloned.param_types[parameter_index]
    cloned.readonly_params = [
        name for name in cloned.readonly_params if name != parameter
    ]
    if hasattr(func, "free_vars"):
        cloned.free_vars = [
            name for name in getattr(func, "free_vars", []) if name != parameter
        ]
    if hasattr(func, "nonlocal_vars"):
        cloned.nonlocal_vars = [
            name for name in getattr(func, "nonlocal_vars", []) if name != parameter
        ]
    _replace_parameter(cloned.body, parameter, class_name)
    if _returns_specialized_type(func, parameter):
        cloned.ret_type = (class_name, None)
    return cloned


def _call_matches(call, is_method: bool, name: str) -> bool:
    if is_method:
        return isinstance(call, A.MethodCall) and call.method == name
    return isinstance(call, A.Call) and call.func == name


def _safe_originals_to_neutralize(mod: A.Module, originals: dict) -> set:
    """Compute the greatest set whose only incoming calls come from that set.

    Calls between generic originals disappear together. Calls from module code,
    generated clones, or a generic original that must remain live keep their
    target live as well. Iterating removals computes the greatest safe set.
    """
    candidates = set(originals)
    changed = True
    while changed:
        changed = False
        for original_id in list(candidates):
            _func, is_method, _owner_name, name = originals[original_id]
            externally_referenced = False
            for caller_id, call in _call_sites_with_owners(mod):
                if not _call_matches(call, is_method, name):
                    continue
                if caller_id in candidates:
                    continue
                externally_referenced = True
                break
            if externally_referenced:
                candidates.remove(original_id)
                changed = True
    return candidates


def _lower_type_parameter_specializations(mod: A.Module) -> None:
    if getattr(mod, "_type_parameter_specializations_lowered", False):
        return
    mod._type_parameter_specializations_lowered = True

    class_names = {cls.name for cls in mod.classes}
    function_defs: dict = {}
    for func in mod.funcs:
        function_defs.setdefault(func.name, []).append(func)
    method_defs: dict = {}
    method_owners: dict = {}
    for cls in mod.classes:
        for method in cls.methods:
            method_defs.setdefault(method.name, []).append(method)
            method_owners.setdefault(method.name, []).append(cls)

    function_targets = {
        name: defs[0]
        for name, defs in function_defs.items()
        if len(defs) == 1 and _type_parameter_indices(defs[0], False)
    }
    method_targets = {
        name: defs[0]
        for name, defs in method_defs.items()
        if len(defs) == 1 and _type_parameter_indices(defs[0], True)
    }
    if not function_targets and not method_targets:
        return

    specializations: dict = {}
    changed = True
    while changed:
        changed = False
        for call in list(_all_call_nodes(mod)):
            is_method = isinstance(call, A.MethodCall)
            name = call.method if is_method else call.func
            func = method_targets.get(name) if is_method else function_targets.get(name)
            if func is None:
                continue
            indices = _type_parameter_indices(func, is_method)
            if not indices:
                continue
            parameter_index = indices[0]
            argument, binding = _argument_binding(
                call,
                func,
                parameter_index,
                is_method,
            )
            if (
                binding is None
                or not isinstance(argument, A.Name)
                or argument.name not in class_names
            ):
                continue

            class_name = argument.name
            owner_name = ""
            owner = None
            if is_method:
                owners = method_owners.get(name, [])
                if len(owners) != 1:
                    continue
                owner = owners[0]
                owner_name = owner.name
            key = (
                is_method,
                owner_name,
                name,
                func.params[parameter_index],
                class_name,
            )
            clone = specializations.get(key)
            if clone is None:
                clone_name = (
                    name
                    + "__asmpy_type_"
                    + _sanitize(func.params[parameter_index])
                    + "_"
                    + _sanitize(class_name)
                )
                clone = _specialized_clone(
                    func,
                    parameter_index,
                    class_name,
                    clone_name,
                )
                specializations[key] = clone
                if is_method:
                    owner.methods.append(clone)
                    if _type_parameter_indices(clone, True):
                        method_targets[clone.name] = clone
                        method_owners[clone.name] = [owner]
                else:
                    mod.funcs.append(clone)
                    if _type_parameter_indices(clone, False):
                        function_targets[clone.name] = clone
            _remove_bound_argument(call, binding)
            if is_method:
                call.method = clone.name
            else:
                call.func = clone.name
            changed = True

    if not specializations:
        return

    originals: dict = {}
    for is_method, owner_name, name, _parameter, _class_name in specializations:
        func = method_targets.get(name) if is_method else function_targets.get(name)
        if func is not None:
            originals[id(func)] = (func, is_method, owner_name, name)

    safe_ids = _safe_originals_to_neutralize(mod, originals)
    for original_id in safe_ids:
        func = originals[original_id][0]
        func.body = [
            A.Return(
                value=A.IntLit(value=0, pos=func.pos, is_none=True),
                pos=func.pos,
            )
        ]
        func.ret_type = None


def _analyze_with_type_parameter_specialization(self: SemaAnalyzer) -> None:
    _lower_type_parameter_specializations(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_type_parameter_specialization_patch", False):
    SemaAnalyzer.analyze = _analyze_with_type_parameter_specialization
    SemaAnalyzer._asmpython_type_parameter_specialization_patch = True
