"""Structural verifier for the neutral SSA IR (``ir.py``).

This checks *well-formedness* of an :class:`~asmpython._compiler.ir.IRModule`
with **no knowledge of any source language** -- it is the contract a frontend
(Python, C, Lua, ...) must satisfy to hand a module to a backend, independent of
how the module was produced. It catches the mistakes a hand-written or
newly-built frontend actually makes: dangling value references, blocks that
don't end in a terminator, branches to non-existent labels, malformed types.

It intentionally does *not* enforce full SSA dominance (the IR uses a memory-SSA
style where locals are ``alloca``+``load``/``store``, so a plain
defined-somewhere check is the right altitude) nor per-op typing rules beyond
type well-formedness -- those would bake in policy the neutral IR doesn't own.

Usage::

    from asmpython._compiler.ir_verify import validate_ir, IRVerifyError
    errors = validate_ir(module, strict=False)   # -> list[str]
    validate_ir(module)                           # raises IRVerifyError if bad
"""

from __future__ import annotations

from .ir import IRBlock, IRFunc, IRInstr, IRModule, IRValue

#: Ops that end a basic block. Every block's last instruction must be one of
#: these, and none may appear earlier.
TERMINATORS = frozenset({"ret", "br", "br.t"})


class IRVerifyError(Exception):
    """Raised by :func:`validate_ir` in strict mode when a module is malformed."""


def _type_ok(value: IRValue, where: str, errors: list[str]) -> None:
    ty = getattr(value, "type", None)
    if ty is None:
        errors.append(f"{where}: value {value.name!r} has no type")
        return
    try:
        # Force the derived properties to run -- an unparseable/garbage type
        # name raises here rather than silently reaching codegen.
        kind = ty.kind
        if kind in ("int", "float", "ptr", "vector"):
            _ = ty.size_bytes
        elif kind == "struct":
            _ = ty.field_offsets
            _ = ty.size_bytes
    except Exception as e:  # noqa: BLE001 -- report, don't crash the verifier
        errors.append(f"{where}: value {value.name!r} has malformed type "
                      f"{ty.name!r}: {e}")


def _check_func(func: IRFunc, errors: list[str]) -> None:
    fn = func.name

    # Every value a frontend may legitimately reference: parameters plus every
    # instruction result (allocas included -- their result is a value too).
    defined: set[str] = {p.name for p in func.params}
    for block in func.blocks:
        for instr in block.instrs:
            if instr.result is not None:
                defined.add(instr.result.name)

    labels = [b.label for b in func.blocks]
    label_set = set(labels)
    if len(labels) != len(label_set):
        dupes = sorted({l for l in labels if labels.count(l) > 1})
        errors.append(f"func {fn!r}: duplicate block labels: {', '.join(dupes)}")
    if not func.blocks:
        errors.append(f"func {fn!r}: has no blocks")

    for param in func.params:
        _type_ok(param, f"func {fn!r} param", errors)

    for block in func.blocks:
        where_b = f"func {fn!r} block {block.label!r}"
        if not block.instrs:
            errors.append(f"{where_b}: empty block (no terminator)")
            continue

        for i, instr in enumerate(block.instrs):
            is_last = i == len(block.instrs) - 1
            where = f"{where_b} instr #{i} ({instr.op!r})"

            # Terminator placement.
            if instr.op in TERMINATORS and not is_last:
                errors.append(f"{where}: terminator not at end of block")
            if is_last and instr.op not in TERMINATORS:
                errors.append(f"{where_b}: last instr {instr.op!r} is not a "
                              f"terminator ({'/'.join(sorted(TERMINATORS))})")

            # Result type well-formedness.
            if instr.result is not None:
                _type_ok(instr.result, where, errors)

            # Operand references: every IRValue operand must be defined; every
            # branch-target label must exist in this function.
            operands = instr.operands or []
            for op in operands:
                if isinstance(op, IRValue) and op.name not in defined:
                    errors.append(f"{where}: references undefined value "
                                  f"{op.name!r}")

            if instr.op == "br":
                _check_labels(instr, operands[0:1], label_set, where, errors)
            elif instr.op == "br.t":
                if operands and not isinstance(operands[0], IRValue):
                    errors.append(f"{where}: br.t condition is not a value")
                _check_labels(instr, operands[1:3], label_set, where, errors)


def _check_labels(instr: IRInstr, candidates, label_set, where, errors) -> None:
    for target in candidates:
        if isinstance(target, str) and target not in label_set:
            errors.append(f"{where}: branch to non-existent block {target!r}")


def validate_ir(module: IRModule, *, strict: bool = True) -> list[str]:
    """Verify ``module`` is well-formed. Returns a list of error strings.

    With ``strict=True`` (default) a non-empty error list raises
    :class:`IRVerifyError`; with ``strict=False`` the list is returned for the
    caller to inspect.
    """
    errors: list[str] = []
    seen_funcs: set[str] = set()
    for func in module.funcs:
        if func.name in seen_funcs:
            errors.append(f"duplicate function name {func.name!r}")
        seen_funcs.add(func.name)
        _check_func(func, errors)

    if strict and errors:
        raise IRVerifyError(
            "IR verification failed:\n  " + "\n  ".join(errors)
        )
    return errors


__all__ = ["validate_ir", "IRVerifyError", "TERMINATORS"]
