"""Elide operations over collection fields proven empty in the live program.

Whole-program compilation can prove that an instance collection initialized
empty is never mutated by any reachable body. Iteration over that field is then
unreachable and may be removed before semantic analysis. This is particularly
useful for callback/event lists when no listener-registration method is used,
but the analysis is name-agnostic and applies to any empty list/dict/set field.
"""

from __future__ import annotations

from . import ast_nodes as A
from .live_definition_compat_fixes import (
    _live_definitions,
    _method_is_implicit_runtime_surface,
)
from .object_flow_compat_fixes import _walk_expression, _walk_statements
from .sema import SemaAnalyzer


_ORIGINAL_ANALYZE = SemaAnalyzer.analyze
_MUTATING_METHODS = {
    "append",
    "extend",
    "insert",
    "remove",
    "pop",
    "clear",
    "sort",
    "reverse",
    "add",
    "discard",
    "update",
    "setdefault",
    "popitem",
}


def _empty_fields(mod: A.Module) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for owner in mod.classes:
        for method in owner.methods:
            if method.name != "__init__":
                continue
            for node in _walk_statements(method.body):
                if not (
                    isinstance(node, A.AttrAssign)
                    and isinstance(node.obj, A.Name)
                    and node.obj.name == "self"
                ):
                    continue
                value = node.value
                empty = (
                    isinstance(value, A.ListLit) and not value.elems
                ) or (
                    isinstance(value, A.DictLit) and not value.keys
                ) or (
                    isinstance(value, A.SetLit) and not value.elems
                )
                if empty:
                    result.setdefault(owner.name, set()).add(node.name)
    return result


def _live_bodies(mod: A.Module):
    live_functions, live_classes, live_methods = _live_definitions(mod)
    for function in mod.funcs:
        if function.name in live_functions:
            yield function.body
    for owner in mod.classes:
        if owner.name not in live_classes:
            continue
        for method in owner.methods:
            if (
                method.name in live_methods
                or _method_is_implicit_runtime_surface(method)
            ):
                yield method.body
    yield mod.body


def _mutated_field_names(mod: A.Module) -> set[str]:
    mutated: set[str] = set()
    for body in _live_bodies(mod):
        for node in _walk_statements(body):
            # Skip the initializing assignment itself; subsequent assignments to
            # a field name make the proof conservative for every owner using it.
            if isinstance(node, A.AttrAssign):
                value = node.value
                initializing_empty = (
                    isinstance(value, A.ListLit) and not value.elems
                ) or (
                    isinstance(value, A.DictLit) and not value.keys
                ) or (
                    isinstance(value, A.SetLit) and not value.elems
                )
                if not initializing_empty:
                    mutated.add(node.name)

            # `self.d[k] = v` / `self.buf[i] = x` -- index assignment into a
            # collection FIELD is a mutation, but it is a statement
            # (A.IndexAssign), not one of the A.MethodCall mutators scanned
            # below. Missing it wrongly "proved" a dict/list field that is
            # populated by subscript-store (the single most common way to fill
            # a dict: `self._d = {}` then `self._d[k] = 1`) still empty, so this
            # pass elided every read/iteration of it -- `self._d[k]` came back
            # as garbage and `for k in self._d` ran zero times.
            if isinstance(node, A.IndexAssign):
                tgt = node.target
                if (
                    isinstance(tgt, A.Subscript)
                    and isinstance(tgt.obj, A.Attr)
                    and isinstance(tgt.obj.obj, A.Name)
                    and tgt.obj.obj.name == "self"
                ):
                    mutated.add(tgt.obj.name)

            expressions = []
            for name in ("expr", "value", "test", "iter", "target", "obj"):
                expression = getattr(node, name, None)
                if expression is not None and not isinstance(expression, str):
                    expressions.extend(_walk_expression(expression))
            for expression in expressions:
                if (
                    isinstance(expression, A.MethodCall)
                    and expression.method in _MUTATING_METHODS
                    and isinstance(expression.obj, A.Attr)
                ):
                    mutated.add(expression.obj.name)
    return mutated


def _field_iter_name(expression) -> "str | None":
    source = expression
    if (
        isinstance(source, A.Call)
        and source.func in ("list", "tuple", "set", "dict")
        and len(source.args) == 1
    ):
        source = source.args[0]
    if (
        isinstance(source, A.Attr)
        and isinstance(source.obj, A.Name)
        and source.obj.name == "self"
    ):
        return source.name
    return None


def _rewrite_statement_list(statements: list, empty_names: set[str]) -> list:
    result: list = []
    for statement in statements:
        if isinstance(statement, A.For):
            field_name = _field_iter_name(statement.iter)
            if field_name in empty_names:
                # The loop executes zero iterations. Preserve its Python `else`
                # clause, which runs when no break occurs.
                result.extend(
                    _rewrite_statement_list(statement.orelse or [], empty_names)
                )
                continue
            statement.body = _rewrite_statement_list(statement.body, empty_names)
            statement.orelse = _rewrite_statement_list(
                statement.orelse or [], empty_names
            )
        elif isinstance(statement, A.While):
            statement.body = _rewrite_statement_list(statement.body, empty_names)
            statement.orelse = _rewrite_statement_list(
                statement.orelse or [], empty_names
            )
        elif isinstance(statement, A.If):
            statement.then = _rewrite_statement_list(statement.then, empty_names)
            statement.orelse = _rewrite_statement_list(
                statement.orelse or [], empty_names
            )
        elif isinstance(statement, A.Try):
            statement.body = _rewrite_statement_list(statement.body, empty_names)
            statement.handler = _rewrite_statement_list(
                statement.handler or [], empty_names
            )
            statement.else_body = _rewrite_statement_list(
                statement.else_body or [], empty_names
            )
            statement.finally_body = _rewrite_statement_list(
                statement.finally_body or [], empty_names
            )
            statement.extra_handlers = [
                (
                    types,
                    binding,
                    _rewrite_statement_list(body, empty_names),
                )
                for types, binding, body in statement.extra_handlers
            ]
        result.append(statement)
    return result


def _elide_proven_empty_fields(mod: A.Module) -> None:
    if getattr(mod, "_proven_empty_fields_elided", False):
        return
    mod._proven_empty_fields_elided = True

    candidates = _empty_fields(mod)
    if not candidates:
        return
    mutated = _mutated_field_names(mod)
    empty_names = {
        field_name
        for fields in candidates.values()
        for field_name in fields
        if field_name not in mutated
    }
    if not empty_names:
        return

    for owner in mod.classes:
        for method in owner.methods:
            method.body = _rewrite_statement_list(method.body, empty_names)
    for function in mod.funcs:
        function.body = _rewrite_statement_list(function.body, empty_names)
    mod.body = _rewrite_statement_list(mod.body, empty_names)


def _analyze_with_empty_collection_elision(self: SemaAnalyzer) -> None:
    _elide_proven_empty_fields(self.mod)
    _ORIGINAL_ANALYZE(self)


if not getattr(SemaAnalyzer, "_asmpython_empty_collection_patch", False):
    SemaAnalyzer.analyze = _analyze_with_empty_collection_elision
    SemaAnalyzer._asmpython_empty_collection_patch = True
