"""Resolve inherited classmethod and staticmethod calls to their defining owner.

A class-valued call such as ``Child.method()`` must link to the class that
actually defines ``method``. For classmethods, Python still binds ``cls`` to the
concrete class used at the call site, not to the defining base class. The core
IR path previously combined the concrete class name with the method name
unconditionally, producing nonexistent symbols such as
``Scene__supports_realm`` when only ``Provider__supports_realm`` was emitted.
"""

from __future__ import annotations

from .. import ast_nodes as A
from .. import ir_lower as IR


_ORIGINAL_LOWER_EXPR = IR._lower_expr


def _method_decorators(ctx, owner: str, method: str) -> set[str]:
    signature = ctx.mctx.classes_sig.get(owner)
    if signature is None:
        return set()
    method_signature = signature.methods.get(method)
    if method_signature is None:
        return set()
    return set(getattr(method_signature, "decorators", []) or [])


def _lower_named_class_method(ctx, expression: A.MethodCall):
    class_name = expression.obj.name
    owner = IR._resolve_method_owner(ctx, class_name, expression.method)
    if owner is None:
        return None

    decorators = _method_decorators(ctx, owner, expression.method)
    if "classmethod" not in decorators and "staticmethod" not in decorators:
        return None

    args = []
    if "classmethod" in decorators:
        class_value = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("const", class_value, [ctx.mctx.class_ids[class_name]]))
        args.append(class_value)
    args.extend(_ORIGINAL_LOWER_EXPR(ctx, argument) for argument in expression.args)

    method_part = getattr(expression, "resolved_overload_symbol", None) or expression.method
    result = ctx.tmp(IR.ir_type_for(A.expr_type(expression)))
    ctx.emit(IR.IRInstr("call", result, [f"{owner}__{method_part}", *args]))
    return result


def _lower_expr_with_inherited_classmethods(ctx, expression):
    if (
        isinstance(expression, A.MethodCall)
        and isinstance(expression.obj, A.Name)
        and expression.obj.name in ctx.mctx.class_ids
    ):
        result = _lower_named_class_method(ctx, expression)
        if result is not None:
            return result
    return _ORIGINAL_LOWER_EXPR(ctx, expression)


if not getattr(IR, "_asmpython_inherited_classmethod_patch", False):
    IR._lower_expr = _lower_expr_with_inherited_classmethods
    IR._asmpython_inherited_classmethod_patch = True
