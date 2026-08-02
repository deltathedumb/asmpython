"""Register-allocation analysis shared by every machine backend.

This is pure IR-level reasoning -- liveness, loop structure, try-region
liveness, eviction choice -- with nothing architecture-specific in it. It was
duplicated between ``x86_64/regalloc.py`` and ``arm64/regalloc.py``, the second
carrying a comment promising it was "kept in step with x86_64/regalloc.py".

It was not. A loop-carried-value fix landed in x86-64 and never reached arm64,
and the divergence was invisible because each backend only ever tested itself.
Measured on ``tests/cases/130_starred_unpack.py`` under ``--passes mem2reg``,
counting pairs of simultaneously-live values assigned the same register:

    x86_64   0
    arm64    949336

Hand-synchronised copies of analysis are how that happens. What genuinely
differs between machines -- register pools, ABI argument registers,
callee-saved sets, register classes, slot sizes -- stays in each backend and is
passed in; what does not differ lives here, once.

RISC-V and MIPS should import this module rather than starting a third and
fourth copy.
"""

from __future__ import annotations

from typing import Any

from ..._compiler.ssa.cfg import successor_indices, try_regions_resolved

_INF = (1 << 30, 1 << 30)


def block_liveness(func: Any) -> "tuple[list[set[str]], list[set[str]]]":
    """(live_in, live_out) per block, by backward dataflow to a fixpoint."""
    succs = successor_indices(func)
    n = len(func.blocks)
    use: list[set[str]] = []
    defined: list[set[str]] = []
    for block in func.blocks:
        u: set[str] = set()
        d: set[str] = set()
        for instr in block.instrs:
            for op in instr.operands:
                if hasattr(op, "name") and op.name not in d:
                    u.add(op.name)
            if instr.result is not None:
                d.add(instr.result.name)
        use.append(u)
        defined.append(d)

    live_in: list[set[str]] = [set() for _ in range(n)]
    live_out: list[set[str]] = [set() for _ in range(n)]
    changing = True
    while changing:
        changing = False
        for bi in range(n - 1, -1, -1):
            out: set[str] = set()
            for si in succs[bi]:
                out |= live_in[si]
            inn = use[bi] | (out - defined[bi])
            if out != live_out[bi] or inn != live_in[bi]:
                live_out[bi], live_in[bi] = out, inn
                changing = True
    return live_in, live_out


def live_before_definition(func: Any) -> set[str]:
    """Values live at a block EARLIER in the list than where they are defined.

    Only a back edge produces this: the value is carried around a loop, so on
    the second and later iterations it is already live when control re-enters
    a block that precedes its own definition in list order.

    Such a value has no representation in this allocator's model. Allocation
    happens when the linear walk reaches the DEFINITION, so the register is
    unowned for the whole span before it -- and on the next iteration whatever
    was handed that register clobbers the carried value. Widening the value's
    last use cannot fix it; the missing reservation is at the START.

    Home them on the stack instead, which is live for the whole function. That
    costs a load per use, and it is the difference between a wrong answer and
    a slow one. Latent while the frontend kept locals in stack slots anyway --
    `mem2reg` promotes exactly these values, which is why it exposed this.
    """
    live_in, _live_out = block_liveness(func)
    def_block: dict[str, int] = {}
    for param in func.params:
        def_block[param.name] = 0
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.result is not None:
                def_block.setdefault(instr.result.name, bi)

    carried: set[str] = set()
    for bi, live in enumerate(live_in):
        for name in live:
            if def_block.get(name, 0) > bi:
                carried.add(name)
    return carried


def last_uses(func: Any) -> dict[str, tuple[int, int]]:
    """Return the (block_idx, instr_idx) of each value's final use.

    A value defined *outside* a loop and read inside it must stay live for
    the whole loop, not just until its textually-last mention: a back edge
    (a br/br.t target at or before the branching block) means every block
    from the target through the branching block re-executes each
    iteration, and since the value's own definition is outside that range,
    nothing inside the loop refreshes it -- it's still needed on the next
    pass even after its "last" textual use. Scanning blocks in plain list
    order misses this: it sees that last mention, concludes the value is
    dead, and lets the allocator reuse its register for something else --
    correct for the textual order, wrong for control flow. Confirmed via a
    live repro: a for-loop's stop/step pointers (defined once before the
    loop) got reassigned to a same-iteration temp inside the increment
    block, corrupting the loop bound on the very next pass.

    This must NOT extend values whose definition is itself inside the same
    loop (the common case: nearly every temporary a loop body computes) --
    those are refreshed every iteration before they're read again, so their
    ordinary textual last-use is already correct, and over-extending them
    would make almost everything in a loop "alive" simultaneously and
    exhaust the register pool for no reason.
    """
    # Loop structure comes from the shared CFG analysis (_compiler/cfg.py):
    # real natural loops, derived from dominance. That replaces an older
    # block-index-range approximation which both invented loops (try/except
    # dispatch branches jump backward by index without being loops -- they
    # needed an explicit try_regions exclusion) and missed loop bodies
    # (ir_lower emits the KeyError raise/ok helper pair at HIGHER indices than
    # the latch, so real body blocks fell outside the assumed span and values
    # used there were never extended across the back edge -- a loop accumulator
    # could silently reset). A branch is a back edge only when its target
    # dominates its source, so both problems are gone by construction.

    def_block: dict[str, int] = {}
    for param in func.params:
        def_block[param.name] = 0
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.result is not None:
                def_block.setdefault(instr.result.name, bi)

    last: dict[str, tuple[int, int]] = {}
    for bi, block in enumerate(func.blocks):
        for ii, instr in enumerate(block.instrs):
            for op in instr.operands:
                if hasattr(op, "name"):
                    last[op.name] = (bi, ii)

    # Extend a use inside a loop to that loop's last block, but only when
    # the value's definition lies outside the loop's own range -- see the
    # docstring for why a loop-internal definition doesn't need this.
    #
    # `end >= bi`, not `end > bi`: a value whose textual last use falls in
    # the loop's OWN LAST block (bi == end) still needs the extension --
    # that block is the one the back edge jumps FROM, so any use inside it
    # (even its very last instruction) still precedes the next iteration's
    # re-entry into the loop. `end > bi` wrongly treated `bi == end` as
    # "not really in a loop that needs extending", leaving the value's
    # last-use exactly where the plain textual scan found it instead of
    # pushing it to the loop's true end -- confirmed via a real repro:
    # sum(xs)'s list pointer (defined once before the loop, read via two
    # `gep`s inside the loop body, the second and textually-last of which
    # sits in the loop's own last block) kept its un-extended last-use,
    # got evicted mid-loop for a value that only lives from that point
    # onward, and its physical register held a stale/wrong value machine
    # code containing a NULL/garbage pointer that segfaulted (confirmed
    # via gdb: the register holding what should have been the list
    # pointer instead held a small loop-counter-like integer) -- the
    # allocator narrowly avoided this everywhere else in the existing
    # test suite by coincidence (the value happening to still be needed
    # for OTHER reasons, or the loop's last block happening to not be
    # where the un-extended value's own last use falls), which is why
    # this specific, narrow shape had never been caught before. Extending
    # to (end, len(instrs)) is always >= (bi, _ii) when bi == end (an
    # instruction index is always < the block's own instruction count),
    # so this is a pure widening -- never moves a value's recorded
    # lifetime backward.
    # Extend a use inside a cycle to that cycle's last block. Each containing
    # loops loses values defined in an outer body and read in an inner one.
    #
    # A value must survive to the end of a cycle when its live range crosses
    # that cycle's back edge, which happens two ways:
    #
    #  1. It is defined OUTSIDE the cycle. Nothing in the cycle refreshes it, so
    #     the next iteration still needs it.
    #  2. It is defined INSIDE the cycle at a LATER block than the use. The
    #     allocator walks blocks in index order, so a use preceding its own
    #     definition can only be reading the previous iteration's value. Phi
    #     elimination creates exactly this: the back-edge copy lands in the
    #     latch while the value is computed in a body block emitted later.
    #     Without this a loop accumulator silently resets (Counter.total()
    #     returning the first element instead of the sum).
    #
    # A value defined inside the cycle and used only after its definition is
    # refreshed every iteration and must NOT be extended, or nearly every loop
    # temporary is pinned live at once and the register pool is exhausted.
    # Loop liveness uses the block-INDEX SPAN of a backward-looking branch, with
    # try/except dispatch excluded. This is the rule that shipped before the
    # natural-loop rework and it is restored deliberately.
    #
    # cfg.py's dominance-based analysis is the correct description of a LOOP, and
    # the passes use it. It is not, on its own, a correct description of
    # LIVENESS here, and substituting it silently miscompiled four programs
    # (r39_running_average's running sum stuck at its first value,
    # 382_nested_listcomp, 425_generator_pipeline, 999_comprehensive_codegen).
    # Replacing it again needs a full differential run, not a case-by-case fix:
    # every variant tried so far trades one set of programs for another --
    # tightening the range frees a register that is still live, and widening it
    # exhausts the pool and crashes the "GP result expected" path instead.
    regions = try_regions_resolved(func)

    def _in_try_region(idx: int) -> bool:
        return any(idx in members for _setjmp, members in regions)

    label_to_idx = {b.label: bi for bi, b in enumerate(func.blocks)}
    loop_start = list(range(len(func.blocks)))
    loop_end = list(range(len(func.blocks)))
    for bi, block in enumerate(func.blocks):
        for instr in block.instrs:
            if instr.op not in ("br", "br.t"):
                continue
            targets = instr.operands[1:] if instr.op == "br.t" else instr.operands
            for target in targets:
                ti = label_to_idx.get(str(target))
                if ti is None or ti > bi or _in_try_region(ti):
                    continue
                for k in range(ti, bi + 1):
                    if loop_start[k] > ti:
                        loop_start[k] = ti
                    if loop_end[k] < bi:
                        loop_end[k] = bi

    for name, (bi, _ii) in list(last.items()):
        start, end = loop_start[bi], loop_end[bi]
        if end >= bi and def_block.get(name, bi) < start:
            last[name] = (end, len(func.blocks[end].instrs))

    # try/except handler blocks are reached via an IMPLICIT control
    # transfer -- a `call _abi_setjmp` followed by a br.t on its result,
    # where the "handler taken" edge only becomes real at runtime via
    # `_abi_raise` -> `_runtime_longjmp` jumping directly back to the
    # setjmp call site, with NO ordinary br/br.t edge from inside the try
    # body to the handler blocks for the plain last-use scan above to
    # see. Worse, ir_lower.py's _lower_try (via ctx.new_block()) allocates
    # the handler blocks AFTER the try's own continuation block in the
    # function's block list (e.g. a for-loop's `Lforcont` increment
    # block, right after the loop body, ends up EARLIER in block-list
    # order than the handler blocks that conceptually run BEFORE it on
    # the exception path) -- so a value defined before the try (e.g. the
    # loop variable's global address, computed once before the loop and
    # reused every iteration) that is ALSO read inside the handler looked,
    # to a plain textual scan, already dead by the time the handler
    # block's def/use got recorded: its last "real" use was the loop's
    # own continuation block, which sits earlier in the list. The
    # allocator then freely reused that value's register for something
    # the handler computes -- confirmed via gdb/objdump: a for loop's own
    # loop-variable address, live in RDI across a `try` whose `except`
    # branch prints that same loop variable, read back as a corrupted/
    # NULL pointer on the very next iteration after the exception fired,
    # segfaulting the following loop's own unrelated code (which reused
    # the same now-scrambled physical register).
    #
    # Fix: ir_lower.py stamps the LABELS of the blocks belonging to each try
    # onto IRFunc.try_regions, and `try_regions_resolved` maps them back to
    # indices in the block list as it stands right now -- so an optimization
    # pass may insert, delete, merge, or reorder blocks in between without
    # silently shifting the region onto the wrong code. Membership is stored
    # rather than a (start, end) span precisely because a span is positional
    # even when its endpoints are labels: block merging can move the end block
    # earlier and collapse the implied range to a fraction of the try.
    # Only extend values actually REFERENCED somewhere
    # inside the region (setjmp_bi, end_bi] -- e.g. read inside a handler
    # block -- to stay live through the region's end; a value merely
    # defined earlier and last used before the try even starts (the
    # common case: nearly everything in the try's own condition/header
    # blocks) has no reason to be pinned all the way through the handler
    # and must NOT be force-extended, or it wrongly outlives its real
    # last use and starves the allocator's register pool / crashes the
    # "GP result expected" assert when a short-lived value like a `br.t`
    # condition gets evicted to a stack slot it was never meant to need.
    try_regions = try_regions_resolved(func)
    if try_regions:
        region_referenced: dict[int, set[str]] = {}
        for bi, block in enumerate(func.blocks):
            for instr in block.instrs:
                for op in instr.operands:
                    if not hasattr(op, "name"):
                        continue
                    for ri, (_setjmp_bi, members) in enumerate(try_regions):
                        if bi in members:
                            region_referenced.setdefault(ri, set()).add(op.name)
        for name, (bi, _ii) in list(last.items()):
            db = def_block.get(name, bi)
            for ri, (setjmp_bi, members) in enumerate(try_regions):
                end_bi = max(members)
                if (
                    db <= setjmp_bi
                    and name in region_referenced.get(ri, ())
                    and end_bi > last[name][0]
                ):
                    last[name] = (end_bi, len(func.blocks[end_bi].instrs))
    return last


def pick_evict(in_reg: dict[str, Any],
                last_use: dict[str, tuple[int, int]],
                now: tuple[int, int]) -> str:
    """Belady: evict the value used furthest in the future (or already dead).

    `<` not `<=` against `now`: a value whose last use IS `now` is being
    read at this very instruction (e.g. as an operand of the instruction
    whose own destination we're allocating a register for) -- not yet
    dead. Using `<=` treated such a value as already-dead-priority
    (_INF), so it could get evicted out from under an instruction still
    reading it as an operand, corrupting the result. Confirmed via a
    direct trace on a real crash (196_hashlib_module.py's MD5 block
    processing): `%t41 = imul(...)` used as `%t43 = iadd(%t41, %t42)`'s
    own operand at the SAME instruction shared its last-use with `now`,
    got evicted, and _dst_gp later asserted on the resulting bogus
    location."""
    _INF = (10**9, 10**9)
    return max(in_reg, key=lambda n: _INF if last_use.get(n, (-1, -1)) < now else last_use[n])




__all__ = ["block_liveness", "last_uses", "live_before_definition", "pick_evict"]
