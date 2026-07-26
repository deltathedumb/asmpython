"""Block-local memory optimizations.

``dse``           dead store elimination
``storeforward``  store-to-load forwarding
``loadelim``      redundant load elimination

All three are **block-local and pointer-identity based**: two accesses are only
treated as the same location when they go through the *same SSA value* as the
pointer. There is no alias analysis here, so anything less certain is left
alone, and any ``call`` (which may write through a pointer it was handed) or
store through a *different* pointer conservatively invalidates everything.

That conservatism is deliberate. The IR carries boxed heap objects whose
aliasing this pass cannot reason about; a wrong answer here is a silent
miscompile, and the wins available from same-pointer redundancy are already
worth having.
"""

from __future__ import annotations

from .._compiler.ir import IRModule, IRPass, IRValue


def _ptr_key(operand) -> "str | None":
    """Identity of a pointer operand, or None if it isn't an SSA value."""
    return operand.name if isinstance(operand, IRValue) else None


class DSEPass(IRPass):
    """Remove a store that is overwritten before anything can read it.

    Within one block, ``store a -> p`` followed by ``store b -> p`` with no
    intervening load, call, or store through another pointer makes the first
    store dead.
    """

    name = "dse"
    description = "remove stores overwritten before any possible read"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            for block in func.blocks:
                # Walk backwards: a store is dead if a later store to the same
                # pointer is reached with nothing that could read in between.
                dead: set[int] = set()
                seen_ptrs: set[str] = set()
                for i in range(len(block.instrs) - 1, -1, -1):
                    instr = block.instrs[i]
                    ops = instr.operands or []
                    if instr.op == "store" and len(ops) > 1:
                        key = _ptr_key(ops[1])
                        if key is None:
                            seen_ptrs.clear()
                            continue
                        if key in seen_ptrs:
                            dead.add(i)          # overwritten below
                        else:
                            seen_ptrs.add(key)
                        continue
                    if instr.op in ("load", "call"):
                        # Either may observe memory: nothing above is provably dead.
                        seen_ptrs.clear()
                if dead:
                    block.instrs = [
                        ins for i, ins in enumerate(block.instrs) if i not in dead
                    ]
                    changed = True
        return changed


class StoreForwardPass(IRPass):
    """Replace a load with the value most recently stored to that pointer.

    ``store v -> p`` ... ``x = load p`` becomes ``x = v``, provided no call or
    store through another pointer intervenes.
    """

    name = "storeforward"
    description = "forward a stored value to a later load of the same pointer"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            replacement: dict[str, object] = {}
            for block in func.blocks:
                stored: dict[str, object] = {}
                kept = []
                for instr in block.instrs:
                    ops = instr.operands or []
                    if instr.op == "store" and len(ops) > 1:
                        key = _ptr_key(ops[1])
                        if key is None:
                            stored.clear()
                        else:
                            # A store through an unknown-aliasing pointer could
                            # hit any other location: keep only this one.
                            stored = {key: ops[0]}
                        kept.append(instr)
                        continue
                    if instr.op == "call":
                        stored.clear()
                        kept.append(instr)
                        continue
                    if instr.op == "load" and ops and instr.result is not None:
                        key = _ptr_key(ops[0])
                        if key is not None and key in stored:
                            value = stored[key]
                            # Only forward when the widths agree -- a narrower
                            # load of a wider store is a truncation, not a copy.
                            try:
                                ok = (isinstance(value, IRValue)
                                      and value.type.name == instr.result.type.name)
                            except Exception:  # noqa: BLE001
                                ok = False
                            if ok:
                                replacement[instr.result.name] = value
                                changed = True
                                continue
                    kept.append(instr)
                block.instrs = kept
            if replacement:
                _apply(func, replacement)
        return changed


class LoadElimPass(IRPass):
    """Remove a second load of a pointer already loaded in this block.

    Complements ``cse``: that pass keys on the whole instruction, this one
    tracks invalidation so a load can still be reused across unrelated
    instructions -- but is dropped as soon as a call or any store appears.
    """

    name = "loadelim"
    description = "reuse a prior load of the same pointer within a block"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            replacement: dict[str, object] = {}
            for block in func.blocks:
                loaded: dict[str, IRValue] = {}
                kept = []
                for instr in block.instrs:
                    ops = instr.operands or []
                    if instr.op in ("store", "call"):
                        loaded.clear()
                        kept.append(instr)
                        continue
                    if instr.op == "load" and ops and instr.result is not None:
                        key = _ptr_key(ops[0])
                        if key is not None:
                            prior = loaded.get(key)
                            if prior is not None:
                                try:
                                    ok = prior.type.name == instr.result.type.name
                                except Exception:  # noqa: BLE001
                                    ok = False
                                if ok:
                                    replacement[instr.result.name] = prior
                                    changed = True
                                    continue
                            loaded[key] = instr.result
                    kept.append(instr)
                block.instrs = kept
            if replacement:
                _apply(func, replacement)
        return changed


def _apply(func, replacement: dict) -> None:
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


__all__ = ["DSEPass", "StoreForwardPass", "LoadElimPass"]
