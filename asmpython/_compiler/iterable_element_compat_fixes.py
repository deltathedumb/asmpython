"""Preserve iterable element types through simple helper and generator patterns.

Two ordinary Python forms carry a statically finite element shape which the
native compiler can resolve before semantic analysis:

* a zero-argument helper returning a module-level literal tuple, and
* a generator method yielding its receiver or another statically typed value.

Normalize the helper call back to its tuple constant so finite-class lowering
can unroll it, and annotate generator returns as list-like iterables with their
yield element type. The runtime already lowers generators through its existing
collection-backed generator machinery; this pass supplies the missing type
metadata only.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A
from .sema import SemaAnalyzer


_ORIGINAL_INIT = SemaAnalyzer.__init__


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
            replacement = A.Name(name=helpers[expression.func], pos=expression.pos)
            node.iter = replacement


def _yield_value_type(value, receiver: str, owner_name: str) -> "str | None":
    if isinstance(value, A.Name) and value.name == receiver:
        return "instance:" + owner_name
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
        return "instance:" + value.func
    return None


def _mark_generator_returns(mod: A.Module) -> None:
    if getattr(mod, "_generator_returns_marked", False):
        return
    mod._generator_returns_marked = True

    for owner in mod.classes:
        for method in owner.methods:
            if method.ret_type is not None or not method.params:
                continue
            receiver = method.params[0]
            yielded: list[str] = []
            for node in _walk(method.body):
                if not isinstance(node, A.YieldStmt):
                    continue
                inferred = _yield_value_type(node.value, receiver, owner.name)
                if inferred is not None:
                    yielded.append(inferred)
            if not yielded:
                continue
            concrete = set(yielded)
            element_type = yielded[0] if len(concrete) == 1 else "any"
            method.ret_type = ("list", element_type)

    for function in mod.funcs:
        if function.ret_type is not None:
            continue
        yielded: list[str] = []
        for node in _walk(function.body):
            if not isinstance(node, A.YieldStmt):
                continue
            inferred = _yield_value_type(node.value, "", "")
            if inferred is not None:
                yielded.append(inferred)
        if yielded:
            concrete = set(yielded)
            function.ret_type = (
                "list",
                yielded[0] if len(concrete) == 1 else "any",
            )


def _init_with_iterable_elements(self: SemaAnalyzer, mod: A.Module, *args, **kwargs) -> None:
    _rewrite_tuple_helper_iterators(mod)
    _mark_generator_returns(mod)
    _ORIGINAL_INIT(self, mod, *args, **kwargs)


if not getattr(SemaAnalyzer, "_asmpython_iterable_element_patch", False):
    SemaAnalyzer.__init__ = _init_with_iterable_elements
    SemaAnalyzer._asmpython_iterable_element_patch = True
