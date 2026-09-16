"""Control-flow graph analysis: predecessors, orderings, dominators, loops.

Computed on demand and cached per function in a `ControlFlowGraph`. Passes and
backends share one instance rather than each rebuilding the same edge lists --
and, more importantly, rather than each writing their own slightly different
version. That divergence is not hypothetical: in the tree this replaces, the
x86-64 and arm64 register allocators kept hand-synchronised copies of the same
liveness analysis until one received a fix the other did not, and the result
was 949,336 pairs of simultaneously-live values sharing a register on arm64
against 0 on x86-64, invisible because each backend only tested itself.

A cache is only safe if invalidation is. `ControlFlowGraph` is built from a
function and holds no reference that survives mutation: a pass that changes the
CFG discards its instance. `passes.manager` does that automatically via the
`preserves` declaration, so a pass that forgets loses performance, not
correctness.

DOMINATORS use Cooper-Harvey-Kennedy: iterate over the reverse-postorder
intersecting predecessor idoms until fixed. It is a few lines, near-linear on
real code, and produces the immediate-dominator tree directly. Lengauer-Tarjan
is asymptotically better and materially harder to get right; at the sizes a
function actually reaches, the constant factor wins anyway.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .module import Block, Function


@dataclass
class ControlFlowGraph:
    """Edge lists and derived orderings for one function.

    Indices, not labels, throughout: a list index is what every consumer wants
    for its own parallel arrays, and the label mapping is kept alongside for
    the boundaries where names are unavoidable.
    """

    function: Function
    blocks: list[Block]
    index_of: dict[str, int]
    successors: list[list[int]]
    predecessors: list[list[int]]

    _rpo: list[int] | None = field(default=None, repr=False)
    _idom: list[int] | None = field(default=None, repr=False)
    _dom_children: dict[int, list[int]] | None = field(default=None, repr=False)

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def build(cls, fn: Function) -> ControlFlowGraph:
        blocks = fn.blocks
        index_of = {b.label: i for i, b in enumerate(blocks)}
        succ: list[list[int]] = []
        for b in blocks:
            # Deduplicated, order preserved. `branch %c, x, x` yields one edge:
            # a predecessor list containing the same block twice makes every
            # dataflow merge count it twice, which is harmless for meet-over-
            # intersection but not for anything counting.
            seen: dict[int, None] = {}
            for label in b.successors:
                j = index_of.get(label)
                if j is not None:
                    seen[j] = None
            succ.append(list(seen))
        pred: list[list[int]] = [[] for _ in blocks]
        for i, targets in enumerate(succ):
            for j in targets:
                pred[j].append(i)
        return cls(fn, blocks, index_of, succ, pred)

    # ── orderings ───────────────────────────────────────────────────────────
    @property
    def reverse_postorder(self) -> list[int]:
        """Reachable blocks, each appearing before all its non-back-edge
        successors. The order dataflow should iterate for fastest convergence."""
        if self._rpo is None:
            self._rpo = self._compute_rpo()
        return self._rpo

    def _compute_rpo(self) -> list[int]:
        if not self.blocks:
            return []
        order: list[int] = []
        seen = [False] * len(self.blocks)
        # Explicit stack: a recursive DFS overflows on a long chain of blocks,
        # which a generated `if/elif` ladder produces easily.
        stack: list[tuple[int, int]] = [(0, 0)]
        seen[0] = True
        while stack:
            node, k = stack.pop()
            if k < len(self.successors[node]):
                stack.append((node, k + 1))
                nxt = self.successors[node][k]
                if not seen[nxt]:
                    seen[nxt] = True
                    stack.append((nxt, 0))
            else:
                order.append(node)
        order.reverse()
        return order

    @property
    def unreachable(self) -> list[int]:
        reachable = set(self.reverse_postorder)
        return [i for i in range(len(self.blocks)) if i not in reachable]

    # ── dominators ──────────────────────────────────────────────────────────
    @property
    def immediate_dominators(self) -> list[int]:
        """`idom[i]` is the index of i's immediate dominator; the entry's is
        itself. Unreachable blocks get -1."""
        if self._idom is None:
            self._idom = self._compute_idom()
        return self._idom

    def _compute_idom(self) -> list[int]:
        n = len(self.blocks)
        if n == 0:
            return []
        idom = [-1] * n
        idom[0] = 0
        rpo = self.reverse_postorder
        position = {b: i for i, b in enumerate(rpo)}

        changed = True
        while changed:
            changed = False
            for b in rpo[1:]:
                candidates = [p for p in self.predecessors[b]
                              if idom[p] != -1 and p in position]
                if not candidates:
                    continue
                new = candidates[0]
                for other in candidates[1:]:
                    new = self._intersect(new, other, idom, position)
                if idom[b] != new:
                    idom[b] = new
                    changed = True
        return idom

    @staticmethod
    def _intersect(a: int, b: int, idom: list[int],
                   position: dict[int, int]) -> int:
        """Walk both up the dominator tree until they meet."""
        while a != b:
            while position[a] > position[b]:
                a = idom[a]
            while position[b] > position[a]:
                b = idom[b]
        return a

    def dominates(self, a: int, b: int) -> bool:
        """True if every path from the entry to `b` passes through `a`."""
        idom = self.immediate_dominators
        if idom[b] == -1:
            return False
        cur = b
        while True:
            if cur == a:
                return True
            nxt = idom[cur]
            if nxt == cur:
                return False
            cur = nxt

    @property
    def dominator_children(self) -> dict[int, list[int]]:
        if self._dom_children is None:
            children: dict[int, list[int]] = {}
            for i, d in enumerate(self.immediate_dominators):
                if i != 0 and d != -1:
                    children.setdefault(d, []).append(i)
            self._dom_children = children
        return self._dom_children

    # ── loops ───────────────────────────────────────────────────────────────
    @property
    def back_edges(self) -> list[tuple[int, int]]:
        """(tail, head) where head dominates tail -- a real loop back edge.

        Defined by dominance rather than by block ORDER. Order-based detection
        is what ties an analysis to the frontend's emission order, so that
        reordering blocks silently changes which loops exist.
        """
        out = []
        for i in range(len(self.blocks)):
            for j in self.successors[i]:
                if self.dominates(j, i):
                    out.append((i, j))
        return out

    def natural_loop(self, tail: int, head: int) -> set[int]:
        """Every block that can reach `tail` without leaving through `head`."""
        body = {head, tail}
        stack = [tail]
        while stack:
            node = stack.pop()
            for p in self.predecessors[node]:
                if p not in body:
                    body.add(p)
                    stack.append(p)
        return body

    def loops(self) -> list[set[int]]:
        return [self.natural_loop(t, h) for t, h in self.back_edges]

    def loop_depth(self) -> list[int]:
        """How many natural loops contain each block. Drives spill priority."""
        depth = [0] * len(self.blocks)
        for body in self.loops():
            for b in body:
                depth[b] += 1
        return depth
