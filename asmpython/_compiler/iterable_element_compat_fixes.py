"""Preserve iterable element types through helpers, generators, and calls."""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A
from .sema import SemaAnalyzer


_ORIGINAL_INIT = SemaAnalyzer.__init__
_ORIGINAL_CHECK_EXPR = SemaAnalyzer._check_expr
_ORIGINAL_ITER_ELEMENT_TYPE = SemaAnalyzer._iter_element_type


def _walk(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    yield value
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(key)
            yield from _walk(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for descriptor in fields(value):
            yield from _walk(getattr(value, descriptor.name))


def _tuple_helpers(mod: A.Module) -> dict[str, str]:
    tuple_names = {
        statement.target
        for statement in mod.body
        if isinstance(statement, A.Assign)
        and isinstance(statement.target, str)
        and isinstance(statement.value, A.TupleLit)
    }
    helpers: dict[str, str] = {}
    for function in mod.funcs:
        if function.params or len(function.body) != 1:
            continue
        statement = function.body[0]
        if (
            isinstance(statement, A.Return)
            and isinstance(statement.value, A.Name)
            and statement.value.name in tuple_names
        ):
            helpers[function.name] = statement.value.name
    return helpers


def _rewrite_tuple_helper_iterators(mod: A.Module) -> None:
    if getattr(mod, "_tuple_helper_iterators_lowered", False):
        return
    mod._tuple_helper_iterators_lowered = True
    helpers = _tuple_helpers(mod)
    if not helpers:
        return
    for node in _walk(mod):
        if isinstance(node, A.For):
            expression = node.iter
        elif isinstance(node, (A.Comprehension, A.DictComprehension)):
            expression = node.iter
        else:
            continue
        if (
            isinstance(expression, A.Call)
            and expression.func in helpers
            and not expression.args
            and not expression.kwargs
            and expression.dstar is None
        ):
            node.iter = A.Name(name=helpers[expression.func], pos=expression.pos)


def _yield_value_type(value, receiver: str, owner_name: str) -> "str | None":
    if isinstance(value, A.Name) and value.name == receiver:
        return owner_name
    if isinstance(value, A.StrLit):
        return "str"
    if isinstance(value, A.FloatLit):
        return "float"
    if isinstance(value, A.IntLit):
        if getattr(value, "is_none", False):
            return None
        return "bool" if getattr(value, "is_bool", False) else "int"
    if isinstance(value, A.ListLit):
        return "list"
    if isinstance(value, A.DictLit):
        return "dict"
    if isinstance(value, A.TupleLit):
        return "tuple"
    if isinstance(value, A.SetLit):
        return "set"
    if isinstance(value, A.Call) and value.func[:1].isupper():
        return value.func
    return None


def _missing_iterable_element(ret_type) -> bool:
    if ret_type is None:
        return True
    if not isinstance(ret_type, tuple) or not ret_type:
        return False
    if ret_type[0] not in ("list", "generator", "iterator", "iterable", "any"):
        return False
    return len(ret_type) < 2 or ret_type[1] is None


def _mark_generator_returns(mod: A.Module) -> None:
    if getattr(mod, "_generator_returns_marked", False):
        return
    mod._generator_returns_marked = True
    for owner in mod.classes:
        for method in owner.methods:
            if not method.params or not _missing_iterable_element(method.ret_type):
                continue
            receiver = method.params[0]
            yielded = []
            for node in _walk(method.body):
                if isinstance(node, A.YieldStmt):
                    inferred = _yield_value_type(node.value, receiver, owner.name)
                    if inferred is not None:
                        yielded.append(inferred)
            if yielded:
                method.ret_type = (
                    "list",
                    yielded[0] if len(set(yielded)) == 1 else "any",
                )
    for function in mod.funcs:
        if not _missing_iterable_element(function.ret_type):
            continue
        yielded = []
        for node in _walk(function.body):
            if isinstance(node, A.YieldStmt):
                inferred = _yield_value_type(node.value, "", "")
                if inferred is not None:
                    yielded.append(inferred)
        if yielded:
            function.ret_type = (
                "list",
                yielded[0] if len(set(yielded)) == 1 else "any",
            )


def _method_element_type(analyzer: SemaAnalyzer, expression: A.MethodCall):
    receiver_type = A.expr_type(expression.obj)
    if receiver_type.startswith("instance:"):
        class_name = receiver_type.split(":", 1)[1]
    elif receiver_type in analyzer.classes:
        class_name = receiver_type
    else:
        return None
    resolved = analyzer._resolve_method(class_name, expression.method)
    if resolved is None:
        return None
    return_type = getattr(resolved[1], "ret_type", None)
    if isinstance(return_type, tuple) and len(return_type) > 1:
        return return_type[1]
    return None


def _check_expr_with_iterable_elements(self: SemaAnalyzer, expression, scope) -> None:
    _ORIGINAL_CHECK_EXPR(self, expression, scope)
    if not isinstance(expression, A.MethodCall):
        return
    if A.expr_type(expression) not in ("list", "tuple", "set"):
        return
    if getattr(expression, "list_el_type", None) not in (None, "any"):
        return
    element_type = _method_element_type(self, expression)
    if element_type not in (None, "any"):
        expression.list_el_type = element_type


def _iter_element_type_with_methods(self: SemaAnalyzer, expression, *args, **kwargs):
    if isinstance(expression, A.MethodCall):
        element_type = _method_element_type(self, expression)
        if element_type not in (None, "any"):
            return element_type
    return _ORIGINAL_ITER_ELEMENT_TYPE(self, expression, *args, **kwargs)


def _init_with_iterable_elements(self: SemaAnalyzer, mod: A.Module, *args, **kwargs) -> None:
    _rewrite_tuple_helper_iterators(mod)
    _mark_generator_returns(mod)
    _ORIGINAL_INIT(self, mod, *args, **kwargs)


if not getattr(SemaAnalyzer, "_asmpython_iterable_element_patch", False):
    SemaAnalyzer.__init__ = _init_with_iterable_elements
    SemaAnalyzer._check_expr = _check_expr_with_iterable_elements
    SemaAnalyzer._iter_element_type = _iter_element_type_with_methods
    SemaAnalyzer._asmpython_iterable_element_patch = True
