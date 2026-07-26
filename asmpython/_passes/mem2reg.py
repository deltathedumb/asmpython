"""mem2reg -- promote stack slots to SSA registers with phi insertion.

asmpython's Python frontend lowers every local to ``alloca`` + ``load``/``store``
("memory SSA", see ``ir_lower.py``'s docstring). That form is always correct but
opaque to optimization: values never flow through the IR, so constant
propagation, GVN, LICM and friends see nothing. This pass performs the classic
promotion -- Cytron et al.'s SSA construction -- turning those slots into real
SSA values joined by ``phi`` nodes at control-flow merges.

It is the prerequisite for essentially every later optimization (the same role
it plays in LLVM, whose ``PromoteMemoryToRegister`` this follows).

Only allocas in the **entry block** that are used *exclusively* as the pointer
operand of ``load``/``store`` are promoted -- matching LLVM's rule. Any other
use (``gep``, passing the address to a ``call``, storing the pointer itself)
means the address escapes, and the slot is left alone.

The backend already understands ``phi`` (``_backends/x86_64/phi_elim.py`` runs
before register allocation), so promoted functions codegen unchanged.

EXPERIMENTAL -- not in the ``o1``/``o2`` presets; request it explicitly.
=======================================================================
This pass is correct; what it exposes is a backend liveness gap. Promotion is
what makes the gap reachable, so until the gap is closed the pass stays out of
the presets (a preset must never silently change behavior).

Two of the three original divergences were genuine allocator bugs and are now
FIXED in ``regalloc.py`` (call-crossing parameters are homed on the stack
instead of pinned to their caller-saved ABI argument register, and
``_take_gp``'s callee-saved requirement is now hard rather than best-effort).
Those fixes also raised the ordinary suite on their own.

The one remaining known divergence is ``_last_uses``'s **index-range loop
detection**: a loop's blocks are not necessarily contiguous in the block list,
and ir_lower emits the raise/ok helper pair for a possibly-KeyError subscript at
*higher* indices than the loop's latch. So in::

    for k in d:
        t = t + d[k]

the accumulator's real use sits outside the assumed span, its liveness is not
extended across the back edge, and the allocator reuses its register mid-loop --
the accumulator silently resets (``Counter.total()`` returns the first value
instead of the sum). Invisible without this pass only because the unoptimized
frontend keeps the accumulator in a stack slot.

Fixing it needs real dominator-based natural loops in the allocator; a raw
predecessor closure over-approximates badly (shared helper blocks pull most of
the function into the "body") and stalls the build. See the KNOWN GAP comment in
``regalloc.py``'s ``_last_uses``.
"""

from __future__ import annotations

from .._compiler.ir import IRInstr, IRModule, IRPass, IRValue
from ._cfg import (
    compute_idom,
    dom_tree_children,
    dominance_frontiers,
    successors,
)


class Mem2RegPass(IRPass):
    name = "mem2reg"
    description = "promote alloca/load/store slots to SSA values (inserts phi)"
    provides = frozenset({"ssa"})
    preserves = frozenset({"cfg"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    # ── candidate discovery ──────────────────────────────────────────────────
    def _candidates(self, func) -> dict[str, object]:
        """Entry-block allocas used only as load/store pointers -> element type."""
        entry_allocas: dict[str, IRValue] = {}
        for instr in func.blocks[0].instrs:
            if instr.op == "alloca" and instr.result is not None:
                entry_allocas[instr.result.name] = instr.result

        if not entry_allocas:
            return {}

        # BACKEND CONSTRAINT (x86-64 regalloc): function parameters are pinned
        # to their incoming ABI argument registers for their whole lifetime
        # (regalloc.py's "Assign function parameters to ABI entry locations"),
        # and those registers are caller-saved. The allocator computes
        # `crosses_call` but does not consult it for parameters, so a parameter
        # that is still live after a `call` is clobbered by the callee.
        #
        # The frontend normally hides this: ir_lower stores every parameter into
        # a stack slot immediately, so the parameter itself dies in the entry
        # block. Promoting that slot is exactly what would extend the
        # parameter's live range across a call -- silently miscompiling any
        # function that uses a parameter after calling something (a recursive
        # `fib` is the classic case).
        #
        # Until the allocator homes call-crossing parameters into callee-saved
        # registers, refuse to promote a slot a parameter is stored into when
        # the function makes any call. Slots holding ordinary computed values
        # are unaffected: the allocator *does* honor `crosses_call` for
        # instruction results.
        has_call = False   # regalloc now homes call-crossing params on the stack
        param_names = {p.name for p in func.params}
        param_backed: set[str] = set()
        if has_call and param_names:
            for block in func.blocks:
                for instr in block.instrs:
                    ops = instr.operands or []
                    if instr.op == "store" and len(ops) > 1:
                        value, ptr = ops[0], ops[1]
                        if (isinstance(value, IRValue) and value.name in param_names
                                and isinstance(ptr, IRValue)
                                and ptr.name in entry_allocas):
                            param_backed.add(ptr.name)

        rejected: set[str] = set()
        load_type: dict[str, object] = {}
        has_load: set[str] = set()

        for block in func.blocks:
            for instr in block.instrs:
                ops = instr.operands or []
                if instr.op == "load":
                    ptr = ops[0] if ops else None
                    # Any *other* operand position referencing an alloca escapes.
                    for other in ops[1:]:
                        if isinstance(other, IRValue) and other.name in entry_allocas:
                            rejected.add(other.name)
                    if isinstance(ptr, IRValue) and ptr.name in entry_allocas:
                        if instr.result is None:
                            rejected.add(ptr.name)
                            continue
                        prev = load_type.get(ptr.name)
                        if prev is not None and prev.name != instr.result.type.name:
                            rejected.add(ptr.name)   # type-punned slot
                        load_type[ptr.name] = instr.result.type
                        has_load.add(ptr.name)
                elif instr.op == "store":
                    # store [value, ptr] -- only the ptr position is a safe use.
                    value = ops[0] if ops else None
                    ptr = ops[1] if len(ops) > 1 else None
                    if isinstance(value, IRValue) and value.name in entry_allocas:
                        rejected.add(value.name)     # the address itself escapes
                    for other in ops[2:]:
                        if isinstance(other, IRValue) and other.name in entry_allocas:
                            rejected.add(other.name)
                    if isinstance(ptr, IRValue) and ptr.name in entry_allocas:
                        if isinstance(value, IRValue):
                            prev = load_type.get(ptr.name)
                            if prev is not None and prev.name != value.type.name:
                                rejected.add(ptr.name)
                elif instr.op == "alloca":
                    continue
                else:
                    for operand in ops:
                        if isinstance(operand, IRValue) and operand.name in entry_allocas:
                            rejected.add(operand.name)

        promotable: dict[str, object] = {}
        for name in entry_allocas:
            if name in rejected or name not in has_load or name in param_backed:
                continue
            ty = load_type.get(name)
            if ty is None:
                continue
            # The slot must hold exactly one scalar of that type.
            size = self._alloca_size(func, name)
            try:
                if size is not None and size != ty.size_bytes:
                    continue
            except Exception:  # noqa: BLE001 -- unknown type width: don't promote
                continue
            promotable[name] = ty
        return promotable

    @staticmethod
    def _alloca_size(func, name: str) -> int | None:
        for instr in func.blocks[0].instrs:
            if (instr.op == "alloca" and instr.result is not None
                    and instr.result.name == name):
                ops = instr.operands or []
                if ops and isinstance(ops[0], int):
                    return ops[0]
                return None
        return None

    # ── the transform ────────────────────────────────────────────────────────
    def _run_func(self, func) -> bool:
        if not func.blocks:
            return False
        promotable = self._candidates(func)
        if not promotable:
            return False

        idom = compute_idom(func)
        if not idom:
            return False
        frontiers = dominance_frontiers(func, idom)
        children = dom_tree_children(idom)
        by_label = {b.label: b for b in func.blocks}
        entry = func.blocks[0].label

        # Blocks that store to each slot -> iterated dominance frontier -> phis.
        defs: dict[str, set[str]] = {name: set() for name in promotable}
        for block in func.blocks:
            for instr in block.instrs:
                ops = instr.operands or []
                if instr.op == "store" and len(ops) > 1:
                    ptr = ops[1]
                    if isinstance(ptr, IRValue) and ptr.name in promotable:
                        defs[ptr.name].add(block.label)

        counter = [0]

        def fresh(slot: str, ty) -> IRValue:
            counter[0] += 1
            return IRValue(f"{slot}__m2r{counter[0]}", ty)

        # phi_for[label][slot] = the phi instruction inserted there
        phi_for: dict[str, dict[str, IRInstr]] = {}
        for slot, ty in promotable.items():
            worklist = list(defs[slot])
            placed: set[str] = set()
            while worklist:
                block_label = worklist.pop()
                for target in frontiers.get(block_label, ()):  # dominance frontier
                    if target in placed or target not in by_label:
                        continue
                    placed.add(target)
                    phi = IRInstr("phi", fresh(slot, ty), [])
                    by_label[target].instrs.insert(0, phi)
                    phi_for.setdefault(target, {})[slot] = phi
                    if target not in defs[slot]:
                        worklist.append(target)

        # Every slot starts as a zero of its type (avoids needing an `undef`).
        stacks: dict[str, list] = {}
        init_instrs: list[IRInstr] = []
        for slot, ty in promotable.items():
            zero = fresh(slot, ty)
            init_instrs.append(IRInstr("const", zero, [0]))
            stacks[slot] = [zero]
        func.blocks[0].instrs[0:0] = init_instrs

        replacement: dict[str, object] = {}

        def resolve(value):
            seen = 0
            while isinstance(value, IRValue) and value.name in replacement:
                value = replacement[value.name]
                seen += 1
                if seen > 10000:  # pathological cycle guard
                    break
            return value

        # ── rename: dominator-tree preorder walk with a per-slot value stack ──
        work: list[tuple[str, str]] = [("enter", entry)]
        pushes: dict[str, list[str]] = {}
        while work:
            action, label = work.pop()
            block = by_label.get(label)
            if block is None:
                continue

            if action == "exit":
                for slot in pushes.pop(label, []):
                    stacks[slot].pop()
                continue

            local_pushes: list[str] = []

            for slot, phi in phi_for.get(label, {}).items():
                stacks[slot].append(phi.result)
                local_pushes.append(slot)

            kept: list[IRInstr] = []
            for instr in block.instrs:
                ops = instr.operands or []
                if instr.op == "load" and ops:
                    ptr = ops[0]
                    if isinstance(ptr, IRValue) and ptr.name in promotable:
                        if instr.result is not None:
                            replacement[instr.result.name] = stacks[ptr.name][-1]
                        continue
                elif instr.op == "store" and len(ops) > 1:
                    ptr = ops[1]
                    if isinstance(ptr, IRValue) and ptr.name in promotable:
                        stacks[ptr.name].append(resolve(ops[0]))
                        local_pushes.append(ptr.name)
                        continue
                elif (instr.op == "alloca" and instr.result is not None
                        and instr.result.name in promotable):
                    continue
                kept.append(instr)
            block.instrs = kept

            # Hand this block's current values to successor phis.
            for succ_label in successors(block):
                for slot, phi in phi_for.get(succ_label, {}).items():
                    phi.operands.extend([stacks[slot][-1], label])

            pushes[label] = local_pushes
            work.append(("exit", label))
            for child in children.get(label, ()):
                work.append(("enter", child))

        # Rewrite every remaining use of a removed load's result.
        if replacement:
            for block in func.blocks:
                for instr in block.instrs:
                    if not instr.operands:
                        continue
                    instr.operands = [
                        resolve(op) if isinstance(op, IRValue) else op
                        for op in instr.operands
                    ]
        return True


__all__ = ["Mem2RegPass"]
