"""Normalize statically typed destructuring before target-neutral IR lowering.

The generic single-iterable TupleAssign path in ``ir_lower.py`` represents every
list/tuple cell as I64. That is correct for opaque container storage, but wrong
for a tuple whose AST carries per-position types and for a literal string whose
elements are one-character strings.

This prepass runs after semantic analysis and rewrites only shapes that can reuse
existing typed parallel-assignment/subscript lowering safely:

* ``a, b = (expr_a, expr_b)`` becomes the ordinary parallel form
  ``a, b = expr_a, expr_b``. Existing lowering evaluates every RHS first and
  stores each real IR type, preserving side-effect and swap semantics.
* ``a, b = pair`` becomes typed tuple subscripts when ``pair`` is a plain name
  with sema-stamped per-slot types. Re-reading a name has no side effects.
* ``a, b = "xy"`` becomes two one-character string literals at compile time.
  Python's host string iteration is already Unicode code-point based, so this is
  exact without introducing an unchecked runtime indexing helper.

Tuple-returning calls, lists, starred targets, mismatched literal lengths, and
non-name targets remain untouched.
"""
from __future__ import annotations

from dataclasses import fields, is_dataclass

from . import ast_nodes as A


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


def _unpack_values(stmt: A.TupleAssign) -> list | None:
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
        # Python strings are Unicode code-point sequences. Splitting the literal
        # with host Python is therefore exact for both ASCII and multibyte UTF-8
        # source text, and avoids exposing exception-sensitive runtime indexing.
        if len(source.value) != len(stmt.targets):
            return None
        return [A.StrLit(character, pos=stmt.pos) for character in source.value]
    return None


def normalize_typed_unpacks(root) -> None:
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
            replacement = _unpack_values(value)
            if replacement is not None:
                value.values = replacement

        for descriptor in fields(value):
            visit(getattr(value, descriptor.name))

    visit(root)


def install_ir_lowering_prepass() -> None:
    """Install the prepass around ``ir_lower.lower_module`` exactly once."""
    from . import ir_lower

    if getattr(ir_lower, "_typed_unpack_normalizer_installed", False):
        return

    original_lower_module = ir_lower.lower_module

    def lower_module(module, **kwargs):
        # Forward **kwargs: this wrapper replaces ir_lower.lower_module
        # wholesale, so any keyword the real one grows (source_file,
        # embed_tracebacks, ...) has to pass through or the prepass silently
        # becomes a signature wall. It swallowed them before, and adding a
        # keyword to lower_module failed with "unexpected keyword argument"
        # from inside this closure rather than anywhere near the caller.
        normalize_typed_unpacks(module)
        return original_lower_module(module, **kwargs)

    ir_lower.lower_module = lower_module
    ir_lower._typed_unpack_normalizer_installed = True
