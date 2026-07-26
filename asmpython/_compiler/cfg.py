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


__all__ = [
    "successors", "successor_indices", "predecessor_indices",
    "reverse_postorder", "dominators", "dominates", "back_edges",
    "natural_loop_body", "natural_loops", "loop_membership",
    "dominance_frontiers",
]
