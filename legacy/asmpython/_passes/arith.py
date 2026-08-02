"""Arithmetic canonicalization and folding passes.

Small InstCombine-family rewrites kept separate from ``peephole`` so each can be
enabled, ordered, and certified on its own:

``reassociate``   canonicalize commutative operands (constant to the right)
``foldchain``     collapse chained constant arithmetic and shifts
``identityconv``  drop width conversions that don't change the type
``negfold``       collapse double negation / double bitwise-not

IMPORTANT (learned the hard way): a constant shift count must be emitted as a
raw ``int`` operand, never a ``const``-defined ``IRValue``. codegen's ``_shift``
only uses the safe immediate encoding when the count is an int; anything else is
treated as a variable-count shift and does an unconditional ``mov rcx, cnt``
with no save/restore.
"""

from __future__ import annotations

from .._compiler.ssa.ir import IRInstr, IRModule, IRPass, IRValue

_COMMUTATIVE = frozenset({"iadd", "imul", "iand", "ior", "ixor"})
_SHIFTS = frozenset({"shl", "shr", "sar"})


def _const_map(func) -> dict:
    out = {}
    for block in func.blocks:
        for instr in block.instrs:
            if (instr.op == "const" and instr.result is not None and instr.operands
                    and isinstance(instr.operands[0], int)
                    and not isinstance(instr.operands[0], bool)):
                out[instr.result.name] = instr.operands[0]
    return out


def _const_of(operand, consts):
    if isinstance(operand, bool):
        return None
    if isinstance(operand, int):
        return operand
    if isinstance(operand, IRValue):
        return consts.get(operand.name)
    return None


def _int_bits(value):
    try:
        ty = value.type
        return ty.bits if ty.kind == "int" else None
    except Exception:  # noqa: BLE001
        return None


def _wrap(raw: int, value) -> "int | None":
    bits = _int_bits(value)
    if not bits:
        return None
    raw &= (1 << bits) - 1
    try:
        if value.type.signed and raw >= (1 << (bits - 1)):
            raw -= 1 << bits
    except Exception:  # noqa: BLE001
        return None
    return raw


class ReassociatePass(IRPass):
    """Put the constant operand of a commutative op on the right.

    Purely a canonicalization: it computes the same value, but makes
    ``c + x`` and ``x + c`` the same shape so ``cse`` can spot them as
    redundant and ``peephole``/``foldchain`` only need to match one form.
    """

    name = "reassociate"
    description = "canonicalize commutative operands (constant to the right)"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            consts = _const_map(func)
            for block in func.blocks:
                for instr in block.instrs:
                    if instr.op not in _COMMUTATIVE:
                        continue
                    ops = instr.operands or []
                    if len(ops) != 2:
                        continue
                    left_const = _const_of(ops[0], consts) is not None
                    right_const = _const_of(ops[1], consts) is not None
                    if left_const and not right_const:
                        instr.operands = [ops[1], ops[0]]
                        changed = True
        return changed


class FoldChainPass(IRPass):
    """Collapse a chain of constant arithmetic into one operation.

    ``(x + c1) + c2`` -> ``x + (c1+c2)``, and ``(x << a) << b`` -> ``x << (a+b)``
    when the combined shift stays in range. Only fires when the inner result has
    exactly one use, so the intermediate really does become dead.
    """

    name = "foldchain"
    description = "collapse chained constant adds/shifts into one op"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        consts = _const_map(func)

        uses: dict[str, int] = {}
        for block in func.blocks:
            for instr in block.instrs:
                for operand in instr.operands or []:
                    if isinstance(operand, IRValue):
                        uses[operand.name] = uses.get(operand.name, 0) + 1

        defs: dict[str, IRInstr] = {}
        for block in func.blocks:
            for instr in block.instrs:
                if instr.result is not None:
                    defs[instr.result.name] = instr

        changed = False
        for block in func.blocks:
            for instr in block.instrs:
                ops = instr.operands or []
                if len(ops) != 2 or instr.result is None:
                    continue
                outer_c = _const_of(ops[1], consts)
                if outer_c is None or not isinstance(ops[0], IRValue):
                    continue
                inner = defs.get(ops[0].name)
                if inner is None or uses.get(ops[0].name, 0) != 1:
                    continue
                inner_ops = inner.operands or []
                if len(inner_ops) != 2:
                    continue
                inner_c = _const_of(inner_ops[1], consts)
                if inner_c is None:
                    continue

                if instr.op == "iadd" and inner.op == "iadd":
                    total = _wrap(inner_c + outer_c, instr.result)
                    if total is None:
                        continue
                    instr.operands = [inner_ops[0], total]
                    changed = True
                elif instr.op == inner.op and instr.op in _SHIFTS:
                    bits = _int_bits(instr.result)
                    total = inner_c + outer_c
                    if bits is None or not (0 < total < bits):
                        continue
                    # Raw int count -- see module docstring.
                    instr.operands = [inner_ops[0], total]
                    changed = True
        return changed


class IdentityConvPass(IRPass):
    """Remove a width conversion whose source and destination types match.

    ``sext``/``zext``/``trunc``/``bitcast`` between identical types computes
    nothing. These appear after other passes rewrite operands, not usually in
    freshly lowered IR.
    """

    name = "identityconv"
    description = "drop sext/zext/trunc/bitcast between identical types"
    preserves = frozenset({"cfg", "ssa"})

    _CONVS = frozenset({"sext", "zext", "trunc", "bitcast_i2f", "bitcast_f2i"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            replacement: dict[str, object] = {}
            for block in func.blocks:
                kept = []
                for instr in block.instrs:
                    ops = instr.operands or []
                    if (instr.op in ("sext", "zext", "trunc")
                            and instr.result is not None and len(ops) == 1
                            and isinstance(ops[0], IRValue)):
                        try:
                            same = ops[0].type.name == instr.result.type.name
                        except Exception:  # noqa: BLE001
                            same = False
                        if same:
                            replacement[instr.result.name] = ops[0]
                            changed = True
                            continue
                    kept.append(instr)
                block.instrs = kept
            if replacement:
                _apply(func, replacement)
        return changed


class NegFoldPass(IRPass):
    """Collapse double negation and double bitwise-not.

    ``ineg(ineg(x))`` and ``inot(inot(x))`` are both ``x``. Only fires when the
    inner value has a single use.
    """

    name = "negfold"
    description = "collapse double ineg / double inot"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            defs: dict[str, IRInstr] = {}
            uses: dict[str, int] = {}
            for block in func.blocks:
                for instr in block.instrs:
                    if instr.result is not None:
                        defs[instr.result.name] = instr
                    for operand in instr.operands or []:
                        if isinstance(operand, IRValue):
                            uses[operand.name] = uses.get(operand.name, 0) + 1

            replacement: dict[str, object] = {}
            for block in func.blocks:
                kept = []
                for instr in block.instrs:
                    ops = instr.operands or []
                    if (instr.op in ("ineg", "inot") and instr.result is not None
                            and len(ops) == 1 and isinstance(ops[0], IRValue)):
                        inner = defs.get(ops[0].name)
                        if (inner is not None and inner.op == instr.op
                                and uses.get(ops[0].name, 0) == 1
                                and inner.operands and len(inner.operands) == 1):
                            replacement[instr.result.name] = inner.operands[0]
                            changed = True
                            continue
                    kept.append(instr)
                block.instrs = kept
            if replacement:
                _apply(func, replacement)
        return changed


def _apply(func, replacement: dict) -> None:
    """Rewrite every operand through `replacement`, following chains."""
    def resolve(value):
        seen = 0
        while isinstance(value, IRValue) and value.name in replacement:
            value = replacement[value.name]
            seen += 1
            if seen > 10000:
                break
        return value

    for block in func.blocks:
        for instr in block.instrs:
            if not instr.operands:
                continue
            instr.operands = [
                resolve(op) if isinstance(op, IRValue) else op
                for op in instr.operands
            ]


__all__ = ["ReassociatePass", "FoldChainPass", "IdentityConvPass", "NegFoldPass"]
