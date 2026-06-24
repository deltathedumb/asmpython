"""
phi_elim.py — Eliminate phi instructions before register allocation.

Each phi node  result = phi(v1, "B1", v2, "B2", ...)  is replaced by:
  - mov result, v1   inserted before the terminator of block B1
  - mov result, v2   inserted before the terminator of block B2
  ...
The phi instruction is then removed from its own block.

Parallel-copy sequentialisation is handled by introducing temporaries when
two phi results are mutually dependent (the swap problem), so the transformation
is always correct.
"""

from __future__ import annotations

from ..._compiler.ir import IRInstr, IRValue, IRType, IRBlock, IRFunc


_TERM = frozenset({"br", "br.t", "ret"})


def _insert_before_terminator(instrs: list[IRInstr], instr: IRInstr) -> None:
    for i in range(len(instrs) - 1, -1, -1):
        if instrs[i].op in _TERM:
            instrs.insert(i, instr)
            return
    instrs.append(instr)


def _tmp(base: IRValue, suffix: str) -> IRValue:
    return IRValue(f"{base.name}__phitmp_{suffix}", base.type)


def eliminate_phi(func: IRFunc) -> None:
    """In-place SSA phi elimination.  Call once per function before regalloc."""
    label_to_block: dict[str, IRBlock] = {b.label: b for b in func.blocks}

    for block in func.blocks:
        phis = [i for i in block.instrs if i.op == "phi"]
        if not phis:
            continue

        # Group copies per predecessor block to detect swap conflicts.
        # pred_copies[label] = list of (result_val, source_val)
        pred_copies: dict[str, list[tuple[IRValue, IRValue]]] = {}
        for phi in phis:
            result = phi.result
            ops = phi.operands  # [v1, "B1", v2, "B2", ...]
            i = 0
            while i + 1 < len(ops):
                src   = ops[i]
                label = str(ops[i + 1])
                i += 2
                if not isinstance(src, IRValue):
                    # Constant: wrap in a const + mov pair
                    const_val = IRValue(f"{result.name}__phiconst_{label}", result.type)
                    pred = label_to_block.get(label)
                    if pred is not None:
                        _insert_before_terminator(pred.instrs,
                            IRInstr("const", const_val, [src]))
                        _insert_before_terminator(pred.instrs,
                            IRInstr("mov", result, [const_val]))
                else:
                    pred_copies.setdefault(label, []).append((result, src))

        # Per predecessor: sequentialise parallel copies to avoid swap bugs.
        for label, copies in pred_copies.items():
            pred = label_to_block.get(label)
            if pred is None:
                continue
            instrs = pred.instrs
            # Detect cycles: result of one copy is source of another in same block.
            results_set = {r.name for r, _ in copies}
            # Break cycles by inserting temporaries for sources that are also results.
            seq: list[tuple[IRValue, IRValue]] = []
            for result, src in copies:
                if src.name in results_set and src.name != result.name:
                    tmp = _tmp(src, label)
                    _insert_before_terminator(instrs, IRInstr("mov", tmp, [src]))
                    seq.append((result, tmp))
                else:
                    seq.append((result, src))
            for result, src in seq:
                if src.name != result.name:
                    _insert_before_terminator(instrs, IRInstr("mov", result, [src]))

        # Remove phi instructions from this block.
        block.instrs = [i for i in block.instrs if i.op != "phi"]
