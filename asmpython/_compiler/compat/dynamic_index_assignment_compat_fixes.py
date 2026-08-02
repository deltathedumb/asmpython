"""Lower string-keyed writes on opaque values as dictionary writes.

Semantic analysis deliberately permits operations on ``any`` values because
Python callers may supply a compatible runtime object. The IR lowerer already
uses the dictionary runtime for dynamic attribute operations such as
``setattr(obj, name, value)``, but rejected the equivalent subscript spelling
``obj[name] = value`` before code generation.

A string key identifies the dictionary/object-field convention unambiguously in
asmpython's runtime. Integer-indexed opaque writes remain rejected because they
could refer to either a list or a user ``__setitem__`` implementation.
"""

from __future__ import annotations

from .. import ast_nodes as A
from .. import ir_lower as IR


_ORIGINAL_LOWER_STMT = IR._lower_stmt


def _lower_stmt_with_dynamic_dict_assignment(ctx, statement) -> None:
    if isinstance(statement, A.IndexAssign):
        target = statement.target
        if (
            not isinstance(target.index, A.Slice)
            and A.expr_type(target.obj) == "any"
            and A.expr_type(target.index) == "str"
        ):
            obj_value = IR._lower_expr(ctx, target.obj)
            key_value = IR._lower_dict_key(ctx, target.index)
            stored_value = IR._lower_expr(ctx, statement.value)
            if A.expr_type(statement.value) == "float":
                integer_bits = ctx.tmp(IR.I64)
                ctx.emit(IR.IRInstr("bitcast_f2i", integer_bits, [stored_value]))
                stored_value = integer_bits
            ctx.emit(
                IR.IRInstr(
                    "call",
                    None,
                    ["_abi_dict_set", obj_value, key_value, stored_value],
                )
            )
            return
    _ORIGINAL_LOWER_STMT(ctx, statement)


if not getattr(IR, "_asmpython_dynamic_index_assignment_patch", False):
    IR._lower_stmt = _lower_stmt_with_dynamic_dict_assignment
    IR._asmpython_dynamic_index_assignment_patch = True
