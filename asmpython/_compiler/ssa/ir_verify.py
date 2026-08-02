"""Structural verifier for the neutral SSA IR (``ir.py``).

This checks *well-formedness* of an :class:`~asmpython._compiler.ssa.ir.IRModule`
with **no knowledge of any source language** -- it is the contract a frontend
(Python, C, Lua, ...) must satisfy to hand a module to a backend, independent of
how the module was produced. It catches the mistakes a hand-written or
newly-built frontend actually makes: dangling value references, blocks that
don't end in a terminator, branches to non-existent labels, malformed types.

It does enforce **SSA dominance** -- every use reachable from its definition --
because that is what makes block order irrelevant, and every optimization pass
depends on it. Without the check, IR that only works by accident of emission
order passes verification and then miscompiles the moment a pass moves a block.
It does not enforce per-op typing rules beyond type well-formedness; those would
bake in policy the neutral IR doesn't own.

Usage::

    from asmpython._compiler.ssa.ir_verify import validate_ir, IRVerifyError
    errors = validate_ir(module, strict=False)   # -> list[str]
    validate_ir(module)                           # raises IRVerifyError if bad
"""

from __future__ import annotations

from .cfg import dominates, dominators, reverse_postorder
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

    # try_regions names blocks by label. A region naming a block that is gone
    # is not itself an error -- a pass may legitimately delete unreachable
    # handler code -- but a malformed entry is, because the register allocator
    # silently ignores what it cannot resolve and the resulting loss of a
    # liveness guarantee only shows up as a segfault at runtime.
    for region in getattr(func, "try_regions", ()) or ():
        if not isinstance(region, tuple) or len(region) != 2:
            errors.append(f"func {fn!r}: malformed try_region {region!r} "
                          f"(want a (setjmp_label, member_labels) pair)")
            continue
        start, members = region
        if not isinstance(start, (str, int)):
            errors.append(f"func {fn!r}: try_region setjmp endpoint {start!r} is "
                          f"neither a block label nor an index")
        if isinstance(members, (str, int)):
            continue                     # legacy (setjmp, end) pair form
        if not isinstance(members, (tuple, list)):
            errors.append(f"func {fn!r}: try_region members {members!r} is not a "
                          f"sequence of block labels")
            continue
        for label in members:
            if not isinstance(label, (str, int)):
                errors.append(f"func {fn!r}: try_region member {label!r} is "
                              f"neither a block label nor an index")

    _check_dominance(func, errors)

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


def _check_dominance(func: IRFunc, errors: list[str]) -> None:
    """Every use must be dominated by its definition.

    This is the invariant that makes block order irrelevant. Without it the IR
    only works by accident of emission order -- the register allocator walks
    blocks in list order, so a definition that merely sits at a lower index
    appears to satisfy a use it does not actually dominate. Any pass that
    reorders, merges, or deletes blocks then breaks the program, and the failure
    surfaces as a wild memory access far from the pass responsible.

    A phi operand is exempt from the ordinary rule: it must dominate the
    predecessor its edge arrives from, not the block holding the phi.
    """
    if not func.blocks:
        return
    def_block: dict[str, int] = {}
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.result is not None:
                def_block.setdefault(instr.result.name, bi)
    if not def_block:
        return

    index = {b.label: i for i, b in enumerate(func.blocks)}
    idom = dominators(func)
    reported: set[tuple[str, int]] = set()

    # Only blocks reachable from entry are checked. Dominance is undefined for
    # unreachable code -- nothing dominates it because no path reaches it -- so
    # every use inside it would be reported, which is noise: the code cannot
    # execute. A pass that leaves a block unreachable is not thereby wrong;
    # `simplifycfg` deletes such blocks later.
    reachable = set(reverse_postorder(func))

    for bi, block in enumerate(func.blocks):
        if bi not in reachable:
            continue
        for instr in block.instrs:
            operands = instr.operands or []
            if instr.op == "phi":
                # (value, incoming_label) pairs: check against the predecessor.
                for k in range(0, len(operands) - 1, 2):
                    value, label = operands[k], operands[k + 1]
                    name = getattr(value, "name", None)
                    pred = index.get(str(label))
                    if name is None or name not in def_block or pred is None:
                        continue
                    db = def_block[name]
                    if db != pred and not dominates(idom, db, pred):
                        key = (name, bi)
                        if key not in reported:
                            reported.add(key)
                            errors.append(
                                f"func {func.name!r} block {block.label!r}: phi "
                                f"operand {name!r} does not dominate its incoming "
                                f"edge from {str(label)!r}")
                continue
            for op in operands:
                name = getattr(op, "name", None)
                if name is None or name not in def_block:
                    continue
                db = def_block[name]
                if db != bi and not dominates(idom, db, bi):
                    key = (name, bi)
                    if key in reported:
                        continue
                    reported.add(key)
                    errors.append(
                        f"func {func.name!r} block {block.label!r}: uses {name!r} "
                        f"defined in {func.blocks[db].label!r}, which does not "
                        f"dominate it")


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
