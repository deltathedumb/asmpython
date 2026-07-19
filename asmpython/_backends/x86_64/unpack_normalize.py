"""Normalize statically typed destructuring before x86-64 IR lowering.

The generic single-iterable TupleAssign path in ``ir_lower.py`` represents every
list/tuple cell as I64. That is correct for opaque container storage, but wrong
for a tuple whose AST carries per-position types and for a literal string whose
elements are one-character strings.

Keep this adaptation backend-local until the target-neutral IR has a first-class
unpack operation. The pass runs after semantic analysis and rewrites only shapes
that can reuse existing typed parallel-assignment/subscript lowering safely:

* ``a, b = (expr_a, expr_b)`` becomes the ordinary parallel form
  ``a, b = expr_a, expr_b``. Existing lowering evaluates every RHS first and
  stores each real IR type, preserving side-effect and swap semantics.
* ``a, b = pair`` becomes typed tuple subscripts when ``pair`` is a plain name
  with sema-stamped per-slot types. Re-reading a name has no side effects.
* ``a, b = "xy"`` becomes parallel string subscripts. Existing subscript
  lowering calls ``_abi_str_char_at`` and returns PTR values.

Tuple-returning calls, lists, starred targets, mismatched literal lengths, and
non-name targets remain untouched.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass

from ..._compiler import ast_nodes as A


def _typed_subscripts(source, types: list[str], stmt: A.TupleAssign) -> list:
    return [
        A.Subscript(
            obj=source,
            index=A.IntLit(index, pos=stmt.pos),
            pos=stmt.pos,
            inferred_type=element_type,
        )
        for index, element_type in enumerate(types)
    ]


def _literal_unpack_values(stmt: A.TupleAssign) -> list | None:
    if len(stmt.values) != 1:
        return None
    if any(isinstance(target, A.StarTarget) for target in stmt.targets):
        return None
    if not all(isinstance(target, A.Name) for target in stmt.targets):
        return None

    source = stmt.values[0]
    if isinstance(source, A.TupleLit):
        if len(source.elems) != len(stmt.targets):
            return None
        return list(source.elems)
    if isinstance(source, A.Name) and source.inferred_type == "tuple":
        element_types = list(source.tuple_elem_types)
        if len(element_types) != len(stmt.targets):
            return None
        return _typed_subscripts(source, element_types, stmt)
    if isinstance(source, A.StrLit):
        # Python strings are Unicode code-point sequences. Host Python's len()
        # gives exactly the target count the existing UTF-8-aware character shim
        # expects; do not rewrite mismatches into unchecked out-of-range reads.
        if len(source.value) != len(stmt.targets):
            return None
        return _typed_subscripts(source, ["str"] * len(stmt.targets), stmt)
    return None


def normalize_literal_unpacks(root) -> None:
    """Rewrite safe typed unpack assignments recursively, in place."""
    seen: set[int] = set()

    def visit(value) -> None:
        if value is None:
            return
        if isinstance(value, (str, int, float, bytes, bool)):
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if isinstance(value, tuple):
            for item in value:
                visit(item)
            return
        if not is_dataclass(value):
            return

        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)

        if isinstance(value, A.TupleAssign):
            replacement = _literal_unpack_values(value)
            if replacement is not None:
                value.values = replacement

        for descriptor in fields(value):
            visit(getattr(value, descriptor.name))

    visit(root)


def install() -> None:
    """Install the prepass around ``ir_lower.lower_module`` exactly once."""
    from ..._compiler import ir_lower

    if getattr(ir_lower, "_x86_literal_unpack_normalizer_installed", False):
        return

    original_lower_module = ir_lower.lower_module

    def lower_module(module):
        normalize_literal_unpacks(module)
        return original_lower_module(module)

    ir_lower.lower_module = lower_module
    ir_lower._x86_literal_unpack_normalizer_installed = True
