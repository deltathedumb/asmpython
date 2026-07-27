"""Canonical control-flow analysis for the neutral IR.

One implementation of successors, dominators, dominance frontiers, and natural
loops, shared by the optimization passes and the backends. Everything here is
language-neutral: it reads only the ``br`` / ``br.t`` / ``ret`` terminators the
IR contract defines, so it is correct for any frontend's module.

Why this module exists
----------------------
Loop structure was previously approximated in the register allocator by the
*block index range* ``[target, source]`` of a backward-looking branch. That
approximation is wrong in both directions:

* **It invents loops.** try/except lowering emits dispatch branches that jump to
  a lower-indexed block without being loops at all, which made whole regions
  look loop-live. The allocator worked around this by ignoring any branch whose
  target fell inside a ``try_regions`` span.
* **It misses loop bodies.** A loop's blocks are not contiguous: ir_lower emits
  the KeyError raise/ok helper pair at *higher* indices than the latch, so real
  body blocks fell outside the assumed span and values used there never had
  their liveness extended across the back edge.

Both disappear under the actual definition. A branch ``latch -> header`` is a
back edge only when **header dominates latch**, which try/except dispatch never
satisfies, so no special-casing is needed. And the natural loop body is derived
from the CFG rather than from index arithmetic, so out-of-order helper blocks
are included.

Note that a predecessor closure *without* the dominance test is not a cheaper
substitute: helper blocks shared between call sites drag most of the function
into the "body", which makes nearly every value loop-live and stalls the
allocator. The dominance test is what makes the closure tight.
"""

from __future__ import annotations

_TERMINATORS = ("br", "br.t")


def successors(block) -> list[str]:
    """Labels this block may branch to (empty for ``ret`` or no terminator)."""
    if not block.instrs:
        return []
    term = block.instrs[-1]
    if term.op == "br":
        return [str(term.operands[0])] if term.operands else []
    if term.op == "br.t":
        return [str(t) for t in term.operands[1:3] if isinstance(t, str)]
    return []


def successor_indices(func) -> list[list[int]]:
    """Successors as block indices, for index-keyed analyses."""
    index_of = {b.label: i for i, b in enumerate(func.blocks)}
    out: list[list[int]] = []
    for block in func.blocks:
        out.append([index_of[s] for s in successors(block) if s in index_of])
    return out


def predecessor_indices(func, succs: "list[list[int]] | None" = None) -> list[list[int]]:
    if succs is None:
        succs = successor_indices(func)
    preds: list[list[int]] = [[] for _ in func.blocks]
    for i, outs in enumerate(succs):
        for s in outs:
            preds[s].append(i)
    return preds


def reverse_postorder(func, succs: "list[list[int]] | None" = None) -> list[int]:
    """Block indices in reverse postorder from the entry block."""
    if not func.blocks:
        return []
    if succs is None:
        succs = successor_indices(func)
    order: list[int] = []
    seen = {0}
    stack = [(0, 0)]
    while stack:
        node, k = stack.pop()
        if k < len(succs[node]):
            stack.append((node, k + 1))
            nxt = succs[node][k]
            if nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, 0))
        else:
            order.append(node)
    order.reverse()
    return order


def dominators(func, succs=None, preds=None) -> dict[int, int]:
    """Immediate dominators by block index (Cooper-Harvey-Kennedy).

    Only reachable blocks appear; the entry block maps to itself.
    """
    if not func.blocks:
        return {}
    if succs is None:
        succs = successor_indices(func)
    if preds is None:
        preds = predecessor_indices(func, succs)

    rpo = reverse_postorder(func, succs)
    order = {node: i for i, node in enumerate(rpo)}
    idom: dict[int, int] = {0: 0}

    def intersect(a: int, b: int) -> int:
        while a != b:
            while order[a] > order[b]:
                a = idom[a]
            while order[b] > order[a]:
                b = idom[b]
        return a

    changed = True
    while changed:
        changed = False
        for node in rpo:
            if node == 0:
                continue
            new = None
            for p in preds[node]:
                if p not in order or p not in idom:
                    continue
                new = p if new is None else intersect(p, new)
            if new is not None and idom.get(node) != new:
                idom[node] = new
                changed = True
    return idom


def dominates(idom: dict[int, int], a: int, b: int) -> bool:
    """True when block ``a`` dominates block ``b``."""
    if a == b:
        return True
    node = b
    while node in idom:
        parent = idom[node]
        if parent == node:          # reached entry
            return False
        if parent == a:
            return True
        node = parent
    return False


def back_edges(func, idom=None, succs=None) -> list[tuple[int, int]]:
    """``(latch, header)`` pairs where the header dominates the latch.

    This is the real definition of a back edge. A branch that merely targets a
    lower-numbered block -- try/except dispatch, for instance -- is not one.
    """
    if succs is None:
        succs = successor_indices(func)
    if idom is None:
        idom = dominators(func, succs)
    edges: list[tuple[int, int]] = []
    for latch, outs in enumerate(succs):
        if latch not in idom:
            continue               # unreachable
        for header in outs:
            if header in idom and dominates(idom, header, latch):
                edges.append((latch, header))
    return edges


def natural_loop_body(latch: int, header: int, preds) -> set[int]:
    """Blocks of the natural loop for back edge ``latch -> header``.

    Everything that reaches the latch without passing through the header, plus
    the header itself.
    """
    body = {header}
    stack: list[int] = []
    if latch != header:
        body.add(latch)
        stack.append(latch)
    while stack:
        node = stack.pop()
        for p in preds[node]:
            if p not in body:
                body.add(p)
                stack.append(p)
    return body


def natural_loops(func) -> list[tuple[int, frozenset[int]]]:
    """Every natural loop as ``(header_index, body_block_indices)``.

    Loops sharing a header are merged, which is the standard treatment for a
    header reached by several latches.
    """
    if not func.blocks:
        return []
    succs = successor_indices(func)
    preds = predecessor_indices(func, succs)
    idom = dominators(func, succs, preds)

    merged: dict[int, set[int]] = {}
    for latch, header in back_edges(func, idom, succs):
        body = natural_loop_body(latch, header, preds)
        if header in merged:
            merged[header] |= body
        else:
            merged[header] = body
    return [(h, frozenset(b)) for h, b in merged.items()]


def loop_membership(func) -> dict[int, frozenset[int]]:
    """Block index -> every block of the loops containing it (union).

    Nested loops union together, so the result for a block inside several loops
    is the outermost extent -- which is what a liveness question wants: a value
    entering any enclosing loop must survive until that loop is done.
    """
    loops = natural_loops(func)
    if not loops:
        return {}
    membership: dict[int, set[int]] = {}
    for _header, body in loops:
        for block in body:
            existing = membership.get(block)
            if existing is None:
                membership[block] = set(body)
            else:
                existing |= body
    return {block: frozenset(body) for block, body in membership.items()}


def dominance_frontiers(func, idom=None, preds=None) -> dict[int, set[int]]:
    """Cytron dominance frontiers, by block index."""
    succs = successor_indices(func)
    if preds is None:
        preds = predecessor_indices(func, succs)
    if idom is None:
        idom = dominators(func, succs, preds)

    frontier: dict[int, set[int]] = {i: set() for i in range(len(func.blocks))}
    for node, plist in enumerate(preds):
        if len(plist) < 2 or node not in idom:
            continue
        for p in plist:
            runner = p
            while runner in idom and runner != idom[node] and runner != node:
                frontier.setdefault(runner, set()).add(node)
                parent = idom[runner]
                if parent == runner:
                    break
                runner = parent
    return frontier


def strongly_connected_components(func, succs=None) -> list[frozenset[int]]:
    """Tarjan's SCCs of the CFG, in reverse topological order.

    Iterative rather than recursive: a large function nests deeper than
    CPython's stack allows.
    """
    if succs is None:
        succs = successor_indices(func)
    n = len(func.blocks)
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    result: list[frozenset[int]] = []
    counter = 0

    for root in range(n):
        if root in index:
            continue
        work: list[tuple[int, int]] = [(root, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = succs[node]
            if child_i < len(children):
                work[-1] = (node, child_i + 1)
                child = children[child_i]
                if child not in index:
                    work.append((child, 0))
                elif child in on_stack:
                    low[node] = min(low[node], index[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == node:
                        break
                result.append(frozenset(component))
    return result


def cycles(func) -> list[frozenset[int]]:
    """Every cycle in the CFG, kept SEPARATE -- nested cycles are not merged.

    Not currently consumed: the register allocator went back to its index-span
    liveness rule after the dominance-based one miscompiled four programs (see
    ``_backends/x86_64/regalloc.py``'s ``_last_uses``). This is kept because it
    is the correct analysis for the retry, and because the nesting property
    below is the specific thing the naive replacement got wrong.

    This is what liveness needs, and the distinction from ``loop_membership`` is
    not cosmetic. That function unions every loop containing a block, and the
    union destroys the nesting: a value defined in an OUTER loop's body and read
    inside an INNER loop looks "defined inside the loop", so it is treated as
    refreshed each iteration and its live range is not extended. It is not
    refreshed -- it still crosses the inner cycle's back edge, and the allocator
    reuses its register mid-loop.

    Lowering produces exactly that shape routinely: a float division emits a
    divide-by-zero guard whose ``ok`` block branches back to the loop's
    continuation, creating an inner cycle nested inside the ordinary loop.
    Observed as ``r39_running_average`` printing 10/1, 10/2, 10/3, 10/4 -- the
    running sum stuck at its first value while the divisor kept incrementing.

    Natural loops (which nest) plus non-trivial SCCs (which catch the
    irreducible cycles dominance-based analysis cannot see) together cover both
    kinds. Overlap between the two is harmless: a caller tests each cycle
    independently.
    """
    out: list[frozenset[int]] = [body for _header, body in natural_loops(func)]
    succs = successor_indices(func)
    for component in strongly_connected_components(func, succs):
        if len(component) > 1:
            pass
        else:
            (only,) = tuple(component)
            if only not in succs[only]:
                continue
        if not any(component <= existing for existing in out):
            out.append(component)
    return out


def cycle_membership(func) -> dict[int, frozenset[int]]:
    """Block index -> the set of blocks in its CFG cycle, or absent if acyclic.

    This is what *liveness* wants, and it is deliberately not
    :func:`loop_membership`. A value read in a block that can re-execute must
    stay live until the whole cycle is done, and that is true of every cycle,
    not only the well-structured ones.

    ``natural_loops`` requires a back edge's target to DOMINATE its source,
    which is the definition of a reducible loop. Lowering emits irreducible
    ones: a float division emits a divide-by-zero guard whose ``ok`` block
    branches back to the loop's continuation, giving the body a second entry, so
    no single header dominates the latch. Analyzing only natural loops silently
    drops those -- the allocator then reuses the accumulator's register
    mid-loop, and a running sum stops accumulating while everything around it
    keeps working (``r39_running_average`` printing 10/1, 10/2, 10/3, 10/4).

    An SCC of size > 1, or a single block branching to itself, is a cycle
    regardless of structure, so this covers both kinds without special-casing
    either. Nested loops collapse into one enclosing SCC, which over-approximates
    the inner loop's extent; that costs some register pressure and is always
    safe, whereas under-approximating corrupts values.
    """
    succs = successor_indices(func)
    membership: dict[int, frozenset[int]] = {}
    for component in strongly_connected_components(func, succs):
        if len(component) > 1:
            for node in component:
                membership[node] = component
        else:
            (only,) = tuple(component)
            if only in succs[only]:
                membership[only] = component
    return membership


def try_regions_resolved(func) -> list[tuple[int, frozenset[int]]]:
    """Resolve ``IRFunc.try_regions`` against the CURRENT block list.

    Returns ``(setjmp_block_index, member_block_indices)`` per region. The
    consumers (both register allocators) reason in block indices, so the stored
    labels are resolved at the moment of the query -- which is the whole point
    of storing labels: a pass may have inserted, deleted, merged, or reordered
    blocks since lowering emitted them.

    Member labels that no longer name a block are dropped. That is correct
    rather than merely convenient: a pass removes a block only when its code is
    unreachable or has been folded elsewhere, and a region over code that cannot
    execute imposes no liveness requirement. A pass that KEEPS the code while
    moving it under another label must rewrite the region instead -- see
    ``rewrite_try_region_labels``.

    Also accepts the older ``(setjmp_label, end_label)`` pair form, expanding it
    to the span it denoted, so an out-of-tree frontend written against the
    previous contract keeps working.
    """
    regions = getattr(func, "try_regions", ()) or ()
    if not regions:
        return []
    index = {block.label: i for i, block in enumerate(func.blocks)}
    n = len(func.blocks)

    def resolve(label):
        if isinstance(label, int):
            return label if 0 <= label < n else None
        return index.get(label)

    out: list[tuple[int, frozenset[int]]] = []
    for entry in regions:
        if not isinstance(entry, tuple) or len(entry) != 2:
            continue
        start, members = entry
        si = resolve(start)
        if si is None:
            continue
        if isinstance(members, (str, int)):
            # legacy pair form: the span (setjmp, end]
            ei = resolve(members)
            if ei is None:
                continue
            lo, hi = (si, ei) if si <= ei else (ei, si)
            resolved = frozenset(range(lo + 1, hi + 1))
        else:
            resolved = frozenset(
                bi for bi in (resolve(m) for m in members) if bi is not None
            )
        if resolved:
            out.append((si, resolved))
    return out


__all__ = [
    "successors", "successor_indices", "predecessor_indices",
    "reverse_postorder", "dominators", "dominates", "back_edges",
    "natural_loop_body", "natural_loops", "loop_membership",
    "dominance_frontiers", "try_regions_resolved",
    "strongly_connected_components", "cycle_membership", "cycles",
]
