"""Lower ``issubclass`` against statically named user classes.

User class values already use the module's stable integer ``class_ids`` at
runtime. ``isinstance`` consumes the same hierarchy metadata through
``_subclass_ids``; ``issubclass`` previously missed a lowering case entirely
and fell through as an unresolved external symbol.

Compile a class-value check into equality tests against the target class and
all of its transitive descendants. Tuple targets are flattened the same way as
Python's ``issubclass(C, (A, B))`` form. Dynamic target-class expressions stay
unsupported rather than being mistaken for native imports.
"""

from __future__ import annotations

from . import ast_nodes as A
from . import ir_lower as IR


_ORIGINAL_LOWER_EXPR = IR._lower_expr


def _target_class_names(ctx, expression) -> list[str] | None:
    if isinstance(expression, A.Name):
        if expression.name in ctx.mctx.class_ids:
            return [expression.name]
        return None
    if isinstance(expression, A.TupleLit):
        names: list[str] = []
        for element in expression.elems:
            nested = _target_class_names(ctx, element)
            if nested is None:
                return None
            for name in nested:
                if name not in names:
                    names.append(name)
        return names
    return None


def _lower_issubclass(ctx, expression: A.Call):
    targets = _target_class_names(ctx, expression.args[1])
    if targets is None:
        raise IR.LowerError(
            "unsupported expr Call (issubclass target must be a user class or tuple of user classes)"
        )

    accepted_ids: list[int] = []
    for target in targets:
        for class_id in IR._subclass_ids(ctx, target):
            if class_id not in accepted_ids:
                accepted_ids.append(class_id)

    candidate = _ORIGINAL_LOWER_EXPR(ctx, expression.args[0])
    result = ctx.tmp(IR.I64)
    ctx.emit(IR.IRInstr("const", result, [0]))
    for class_id in accepted_ids:
        expected = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("const", expected, [class_id]))
        matches = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("icmp.eq", matches, [candidate, expected]))
        combined = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("ior", combined, [result, matches]))
        result = combined
    return result


def _lower_expr_with_issubclass(ctx, expression):
    if (
        isinstance(expression, A.Call)
        and expression.func == "issubclass"
        and len(expression.args) == 2
    ):
        return _lower_issubclass(ctx, expression)
    return _ORIGINAL_LOWER_EXPR(ctx, expression)


if not getattr(IR, "_asmpython_issubclass_patch", False):
    IR._lower_expr = _lower_expr_with_issubclass
    IR._asmpython_issubclass_patch = True
