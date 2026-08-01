"""The verifier: everything a backend is allowed to assume.

This is the contract. A backend author should be able to read the list below,
assume every item, and write code that handles nothing else -- no defensive
checks, no "what if the frontend emitted garbage". If a module passes
`verify()`, all of this holds:

  1.  Every block ends in exactly one terminator, and no terminator appears
      anywhere but last.
  2.  Every branch target names a block in the same function.
  3.  Every register read is declared in `func.regs`, and READ AFTER A WRITE on
      every path that reaches it.
  4.  Every register has one type for its whole life.
  5.  Operand counts and types match the opcode's `Spec`.
  6.  `ty` is a type the opcode permits.
  7.  Comparisons define an i1; `branch` reads an i1.
  8.  `call` names a function in the module (or an external declaration).
  9.  The entry block is not a branch target -- it has no predecessors, so a
      backend can emit the prologue there without checking.
  10. A function whose return type is not void ends every path in `ret` with a
      value of that type.

Rule 3 is the one worth dwelling on. Without SSA a register may be assigned
many times, so "declared" is not enough -- reading a register before anything
has written it on some path is exactly the bug that mutable registers make easy
and that produces garbage at runtime rather than a crash. The check is a
forward dataflow over the CFG, so it costs a little more than a scan and is
worth every cycle.

Errors accumulate. A frontend under development gets the whole list, not the
first one and a new run for each.
"""
from __future__ import annotations

from . import ops, types as T
from .core import Block, Func, Instr, Module
from .ops import Op, Sig


class VerifyError(Exception):
    """One or more IR invariants were violated. `problems` lists them all."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        head = problems[0] if problems else "(none)"
        more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
        super().__init__(f"{len(problems)} IR problem(s): {head}{more}")

    def report(self) -> str:
        return "\n".join(f"  {p}" for p in self.problems)


def verify(module: Module) -> None:
    """Raise VerifyError if `module` violates any invariant above."""
    problems: list[str] = []
    known = {f.name for f in module.funcs}
    globs = {g.name for g in module.globals}
    for func in module.funcs:
        if not func.external:
            _func(func, known, globs, problems)
    if problems:
        raise VerifyError(problems)


def _func(fn: Func, known: set[str], globs: set[str], out: list[str]) -> None:
    where = fn.name

    if not fn.blocks:
        out.append(f"{where}: has no blocks (declare it external instead)")
        return

    labels = [b.label for b in fn.blocks]
    dupes = {l for l in labels if labels.count(l) > 1}
    for d in sorted(dupes):
        out.append(f"{where}: duplicate block label {d!r}")
    label_set = set(labels)

    for p in fn.params:
        if p not in fn.regs:
            out.append(f"{where}: parameter %{p} has no declared type")

    # ── per-block structure and per-instruction shape ───────────────────────
    for blk in fn.blocks:
        _block(fn, blk, label_set, known, globs, out)

    # ── the entry block must have no predecessors (invariant 9) ─────────────
    entry = fn.blocks[0].label
    for blk in fn.blocks:
        if entry in blk.successors:
            out.append(
                f"{where}/{blk.label}: branches to the ENTRY block {entry!r}. "
                f"The entry block is where a backend puts the prologue, so it "
                f"must not be re-entered; jump to a copy instead"
            )
            break

    # ── invariant 3: no register is read before it is written ──────────────
    _check_reads_after_writes(fn, out)


def _block(fn: Func, blk: Block, labels: set[str], known: set[str],
           globs: set[str], out: list[str]) -> None:
    where = f"{fn.name}/{blk.label}"

    if not blk.instrs:
        out.append(f"{where}: empty block; every block needs a terminator")
        return

    for i, ins in enumerate(blk.instrs):
        last = i == len(blk.instrs) - 1
        if ins.is_terminator and not last:
            out.append(
                f"{where}: {ins.op.value!r} is a terminator but is followed by "
                f"{len(blk.instrs) - i - 1} more instruction(s)"
            )
        if last and not ins.is_terminator:
            out.append(
                f"{where}: block ends with {ins.op.value!r}, which is not a "
                f"terminator; add jump/branch/ret/unreachable"
            )
        _instr(fn, where, ins, labels, known, globs, out)


def _instr(fn: Func, where: str, ins: Instr, labels: set[str],
           known: set[str], globs: set[str], out: list[str]) -> None:
    spec = ops.spec(ins.op)
    name = ins.op.value

    # `ty` must be one the opcode allows.
    if spec.allowed and ins.ty not in spec.allowed:
        out.append(
            f"{where}: {name} cannot operate on {ins.ty}; allowed: "
            + ", ".join(t.name for t in spec.allowed)
        )

    # Arity.
    if spec.arity is not None and len(ins.args) != spec.arity:
        out.append(
            f"{where}: {name} takes {spec.arity} operand(s), got {len(ins.args)}"
        )

    # Every operand must name a declared register.
    for a in ins.args:
        if a not in fn.regs:
            out.append(f"{where}: {name} reads undeclared register %{a}")

    # Result.
    if spec.defines_value:
        want = T.I1 if spec.result == "i1" else ins.ty
        if ins.op is Op.CALL and ins.ty.is_void:
            if ins.dst is not None:
                out.append(f"{where}: void call cannot define %{ins.dst}")
        elif ins.dst is None:
            out.append(f"{where}: {name} must define a register")
        elif ins.dst in fn.regs and fn.regs[ins.dst] != want:
            out.append(
                f"{where}: {name} defines %{ins.dst} as {fn.regs[ins.dst]} but "
                f"produces {want}"
            )
    elif ins.dst is not None:
        out.append(f"{where}: {name} defines nothing but was given %{ins.dst}")

    # Operand types, per signature shape.
    def ty_of(reg: int) -> T.Type | None:
        return fn.regs.get(reg)

    if spec.sig in (Sig.SAME, Sig.SAME_ONE):
        for a in ins.args:
            t = ty_of(a)
            if t is not None and t != ins.ty:
                out.append(
                    f"{where}: {name} at {ins.ty} was given %{a} of type {t}"
                )
    elif spec.sig is Sig.CMP:
        for a in ins.args:
            t = ty_of(a)
            if t is not None and t != ins.ty:
                out.append(
                    f"{where}: {name} compares {ins.ty} values but %{a} is {t}"
                )
    elif spec.sig is Sig.CONV_ONE:
        if ins.args:
            t = ty_of(ins.args[0])
            if t is not None and t == ins.ty:
                out.append(
                    f"{where}: {name} from {t} to the same type is a no-op; "
                    f"use copy"
                )
            if t is not None and ins.op is Op.BITCAST and t.size != ins.ty.size:
                out.append(
                    f"{where}: bitcast {t} -> {ins.ty} changes width "
                    f"({t.size} -> {ins.ty.size} bytes)"
                )
    elif spec.sig is Sig.PTR_ONE:
        if ins.args:
            t = ty_of(ins.args[0])
            if t is not None and not t.is_ptr:
                out.append(f"{where}: {name} needs a ptr, got %{ins.args[0]}:{t}")
    elif spec.sig is Sig.PTR_INT:
        if len(ins.args) == 2:
            b, o = ty_of(ins.args[0]), ty_of(ins.args[1])
            if b is not None and not b.is_ptr:
                out.append(f"{where}: {name} base must be ptr, got {b}")
            if o is not None and not o.is_int:
                out.append(f"{where}: {name} offset must be an integer, got {o}")
    elif spec.sig is Sig.STORE:
        if len(ins.args) == 2:
            v, a = ty_of(ins.args[0]), ty_of(ins.args[1])
            if v is not None and v != ins.ty:
                out.append(f"{where}: store of {ins.ty} given %{ins.args[0]}:{v}")
            if a is not None and not a.is_ptr:
                out.append(f"{where}: store address must be ptr, got {a}")

    # Symbols and labels.
    if ins.op is Op.CALL:
        if not ins.sym:
            out.append(f"{where}: call has no symbol")
        elif ins.sym not in known:
            out.append(f"{where}: call to unknown function {ins.sym!r}")
    if ins.op is Op.GLOBAL_ADDR:
        if not ins.sym:
            out.append(f"{where}: global_addr has no symbol")
        elif ins.sym not in globs:
            out.append(f"{where}: global_addr of unknown global {ins.sym!r}")
    if ins.op is Op.FUNC_ADDR and ins.sym not in known:
        out.append(f"{where}: func_addr of unknown function {ins.sym!r}")

    for lbl in ins.labels:
        if lbl not in labels:
            out.append(f"{where}: {name} targets unknown block {lbl!r}")
    for _, lbl in ins.cases:
        if lbl not in labels:
            out.append(f"{where}: switch case targets unknown block {lbl!r}")

    if ins.op is Op.JUMP and len(ins.labels) != 1:
        out.append(f"{where}: jump needs exactly 1 target, got {len(ins.labels)}")
    if ins.op is Op.BRANCH and len(ins.labels) != 2:
        out.append(f"{where}: branch needs 2 targets, got {len(ins.labels)}")
    if ins.op is Op.CONST and ins.imm is None:
        out.append(f"{where}: const has no value")
    if ins.op is Op.ALLOCA and not isinstance(ins.imm, int):
        out.append(f"{where}: alloca needs a byte count, got {ins.imm!r}")

    # RET must agree with the function's return type.
    if ins.op is Op.RET:
        if fn.ret.is_void and ins.args:
            out.append(f"{where}: {fn.name} returns void but ret has a value")
        elif not fn.ret.is_void:
            if not ins.args:
                out.append(f"{where}: {fn.name} returns {fn.ret} but ret is bare")
            else:
                t = ty_of(ins.args[0])
                if t is not None and t != fn.ret:
                    out.append(
                        f"{where}: {fn.name} returns {fn.ret} but ret gives {t}"
                    )


def _check_reads_after_writes(fn: Func, out: list[str]) -> None:
    """Invariant 3: no path reads a register before writing it.

    Forward dataflow: a register is "available" on entry to a block only if it
    is available on exit from EVERY predecessor. Parameters are available
    everywhere. Iterated to a fixed point, so loops converge rather than
    reporting a false positive on the back edge.

    This is the check mutable registers make necessary. With SSA, a use before
    a def is structurally impossible; here it is a plain typo that produces
    whatever happened to be in the register.
    """
    index = {b.label: i for i, b in enumerate(fn.blocks)}
    preds: dict[int, list[int]] = {i: [] for i in range(len(fn.blocks))}
    for i, b in enumerate(fn.blocks):
        for s in b.successors:
            if s in index:
                preds[index[s]].append(i)

    params = set(fn.params)
    # Start optimistic everywhere but the entry, so loops converge downward.
    all_regs = set(fn.regs)
    avail_in: list[set[int]] = [set(all_regs) for _ in fn.blocks]
    avail_out: list[set[int]] = [set(all_regs) for _ in fn.blocks]
    if fn.blocks:
        avail_in[0] = set(params)

    for _ in range(len(fn.blocks) + 2):
        changed = False
        for i, blk in enumerate(fn.blocks):
            if i == 0:
                cur = set(params)
            elif preds[i]:
                cur = set.intersection(*(avail_out[p] for p in preds[i]))
            else:
                cur = set(params)   # unreachable: do not report cascading noise
            if cur != avail_in[i]:
                avail_in[i] = cur
                changed = True
            cur = set(cur)
            for ins in blk.instrs:
                if ins.dst is not None:
                    cur.add(ins.dst)
            if cur != avail_out[i]:
                avail_out[i] = cur
                changed = True
        if not changed:
            break

    reported: set[tuple[str, int]] = set()
    for i, blk in enumerate(fn.blocks):
        live = set(avail_in[i])
        for ins in blk.instrs:
            for a in ins.args:
                if a in fn.regs and a not in live and (blk.label, a) not in reported:
                    reported.add((blk.label, a))
                    out.append(
                        f"{fn.name}/{blk.label}: reads %{a} before any path "
                        f"writes it ({ins.op.value})"
                    )
            if ins.dst is not None:
                live.add(ins.dst)
