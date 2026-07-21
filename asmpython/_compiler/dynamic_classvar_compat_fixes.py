"""Dispatch class-variable reads through runtime class IDs.

Classmethod receivers are represented by asmpython's stable integer class IDs.
A body such as ``return cls.value`` therefore cannot use the ordinary instance
attribute dictionary path: the receiver is a class value, not an instance
pointer. Direct ``ClassName.value`` reads already lower to dedicated class-var
globals; this pass extends the same representation to dynamic class values.

Reachability filtering clones class methods before IR lowering. Preserve their
decorators and mark the exact first parameter of each active classmethod on its
function context, so only that receiver uses class-ID attribute dispatch. For
each concrete runtime class ID, resolve the nearest class in its inheritance
chain that defines the requested variable, preserving overrides and inheritance.
"""

from __future__ import annotations

from . import ast_nodes as A
from . import ir_lower as IR


_ORIGINAL_LOWER_EXPR = IR._lower_expr
_ORIGINAL_REACHABLE_CALLABLES = IR._reachable_callables
_ORIGINAL_LOWER_FUNC = IR.lower_func
_ORIGINAL_CTX_INIT = IR._FuncCtx.__init__
_ACTIVE_CLASSMETHOD_RECEIVER: str | None = None


def _reachable_callables_with_method_metadata(mod):
    top_functions, method_functions = _ORIGINAL_REACHABLE_CALLABLES(mod)
    originals = {
        f"{owner.name}__{method.name}": method
        for owner in mod.classes
        for method in owner.methods
    }
    for lowered in method_functions:
        source = originals.get(lowered.name)
        if source is None:
            continue
        lowered.decorators = list(source.decorators)
        if "classmethod" in source.decorators and lowered.params:
            parameter_types = list(lowered.param_types)
            while len(parameter_types) < len(lowered.params):
                parameter_types.append(None)
            parameter_types[0] = ("type", None, None, [], None)
            lowered.param_types = parameter_types
    return top_functions, method_functions


def _ctx_init_with_classmethod_receiver(self, *args, **kwargs) -> None:
    _ORIGINAL_CTX_INIT(self, *args, **kwargs)
    self.classmethod_receiver = _ACTIVE_CLASSMETHOD_RECEIVER


def _lower_func_with_classmethod_receiver(function, module_context, **kwargs):
    global _ACTIVE_CLASSMETHOD_RECEIVER
    previous = _ACTIVE_CLASSMETHOD_RECEIVER
    if "classmethod" in getattr(function, "decorators", []) and function.params:
        _ACTIVE_CLASSMETHOD_RECEIVER = function.params[0]
    else:
        _ACTIVE_CLASSMETHOD_RECEIVER = None
    try:
        return _ORIGINAL_LOWER_FUNC(function, module_context, **kwargs)
    finally:
        _ACTIVE_CLASSMETHOD_RECEIVER = previous


def _parent_name(ctx, class_name: str) -> str | None:
    signature = ctx.mctx.classes_sig.get(class_name)
    if signature is None:
        return None
    parent = getattr(signature, "parent", None)
    if isinstance(parent, str) and parent:
        return parent
    return None


def _class_var_owner(ctx, class_name: str, attribute: str) -> str | None:
    current = class_name
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        if (current, attribute) in ctx.mctx.class_var_labels:
            return current
        current = _parent_name(ctx, current)
    return None


def _dynamic_class_var_rows(ctx, attribute: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for class_name, class_id in ctx.mctx.class_ids.items():
        owner = _class_var_owner(ctx, class_name, attribute)
        if owner is None:
            continue
        rows.append((class_id, ctx.mctx.class_var_labels[(owner, attribute)]))
    rows.sort(key=lambda row: row[0])
    return rows


def _lower_dynamic_class_var(ctx, expression: A.Attr):
    rows = _dynamic_class_var_rows(ctx, expression.name)
    if not rows:
        return None

    class_value = _ORIGINAL_LOWER_EXPR(ctx, expression.obj)
    result_type = IR.ir_type_for(A.expr_type(expression))
    result_slot = ctx.ensure_slot(f"__classvar_result_{id(expression)}", result_type)

    check_blocks = [ctx.new_block(f"classvarcheck{i}") for i in range(len(rows))]
    hit_blocks = [ctx.new_block(f"classvarhit{i}") for i in range(len(rows))]
    default_block = ctx.new_block("classvardefault")
    end_block = ctx.new_block("classvarend")

    ctx.emit(IR.IRInstr("br", None, [check_blocks[0].label]))
    for index, (class_id, label) in enumerate(rows):
        ctx.switch_to(check_blocks[index])
        expected = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("const", expected, [class_id]))
        matches = ctx.tmp(IR.I64)
        ctx.emit(IR.IRInstr("icmp.eq", matches, [class_value, expected]))
        next_label = (
            check_blocks[index + 1].label
            if index + 1 < len(check_blocks)
            else default_block.label
        )
        ctx.emit(
            IR.IRInstr(
                "br.t",
                None,
                [matches, hit_blocks[index].label, next_label],
            )
        )

        ctx.switch_to(hit_blocks[index])
        pointer = ctx.tmp(IR.PTR)
        ctx.emit(IR.IRInstr("global_addr", pointer, [label]))
        value = ctx.tmp(ctx.mctx.global_types.get(label, result_type))
        ctx.emit(IR.IRInstr("load", value, [pointer]))
        if value.type is not result_type:
            if value.type is IR.F64 and result_type is IR.I64:
                converted = ctx.tmp(IR.I64)
                ctx.emit(IR.IRInstr("bitcast_f2i", converted, [value]))
                value = converted
            elif value.type is IR.I64 and result_type is IR.F64:
                converted = ctx.tmp(IR.F64)
                ctx.emit(IR.IRInstr("bitcast_i2f", converted, [value]))
                value = converted
        ctx.emit(IR.IRInstr("store", None, [value, result_slot]))
        ctx.emit(IR.IRInstr("br", None, [end_block.label]))

    ctx.switch_to(default_block)
    zero = ctx.tmp(result_type)
    ctx.emit(IR.IRInstr("const", zero, [0]))
    ctx.emit(IR.IRInstr("store", None, [zero, result_slot]))
    ctx.emit(IR.IRInstr("br", None, [end_block.label]))

    ctx.switch_to(end_block)
    result = ctx.tmp(result_type)
    ctx.emit(IR.IRInstr("load", result, [result_slot]))
    return result


def _lower_expr_with_dynamic_classvars(ctx, expression):
    receiver_name = getattr(ctx, "classmethod_receiver", None)
    if (
        receiver_name is not None
        and isinstance(expression, A.Attr)
        and isinstance(expression.obj, A.Name)
        and expression.obj.name == receiver_name
    ):
        result = _lower_dynamic_class_var(ctx, expression)
        if result is not None:
            return result
    return _ORIGINAL_LOWER_EXPR(ctx, expression)


if not getattr(IR, "_asmpython_dynamic_classvar_patch", False):
    IR._reachable_callables = _reachable_callables_with_method_metadata
    IR._FuncCtx.__init__ = _ctx_init_with_classmethod_receiver
    IR.lower_func = _lower_func_with_classmethod_receiver
    IR._lower_expr = _lower_expr_with_dynamic_classvars
    IR._asmpython_dynamic_classvar_patch = True
