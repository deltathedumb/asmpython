"""Label-keyed views over the canonical CFG analysis.

The real implementation lives in ``asmpython/_compiler/cfg.py`` and is shared
with the backends -- there is exactly one dominator/natural-loop implementation
in the compiler, and this module must not grow a second one.

Passes address blocks by **label** (they rewrite branch operands, which are
labels), while the backend addresses them by **index** (its allocator walks the
block list positionally). This module is the thin label-keyed adapter; anything
genuinely new about control flow belongs in ``_compiler/cfg.py``.
"""

from __future__ import annotations

from .._compiler.ssa.cfg import (
    dominance_frontiers as _dominance_frontiers_idx,
    dominators as _dominators_idx,
    natural_loops as _natural_loops_idx,
    successors,
)

__all__ = [
    "successors", "build_preds", "reverse_postorder",
    "compute_idom", "dominance_frontiers", "dom_tree_children",
    "natural_loops",
]


def build_preds(func) -> dict[str, list[str]]:
    preds: dict[str, list[str]] = {b.label: [] for b in func.blocks}
    for block in func.blocks:
        for succ in successors(block):
            if succ in preds and block.label not in preds[succ]:
                preds[succ].append(block.label)
    return preds


def reverse_postorder(func) -> list[str]:
    from .._compiler.ssa.cfg import reverse_postorder as _rpo_idx

    labels = [b.label for b in func.blocks]
    return [labels[i] for i in _rpo_idx(func)]


def compute_idom(func) -> dict[str, str]:
    """Immediate dominators, keyed by label. Entry maps to itself."""
    labels = [b.label for b in func.blocks]
    return {
        labels[node]: labels[parent]
        for node, parent in _dominators_idx(func).items()
    }


def dominance_frontiers(func, idom: dict[str, str] | None = None) -> dict[str, set[str]]:
    """Cytron dominance frontiers, keyed by label.

    ``idom`` is accepted for call-site compatibility and ignored: the canonical
    analysis recomputes what it needs, and passing a stale map would be a way to
    get subtly wrong answers.
    """
    labels = [b.label for b in func.blocks]
    return {
        labels[node]: {labels[t] for t in targets}
        for node, targets in _dominance_frontiers_idx(func).items()
    }


def dom_tree_children(idom: dict[str, str]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {label: [] for label in idom}
    for label, parent in idom.items():
        if parent != label:
            children.setdefault(parent, []).append(label)
    return children


def natural_loops(func) -> list[tuple[str, frozenset[str]]]:
    """Natural loops as ``(header_label, body_labels)``."""
    labels = [b.label for b in func.blocks]
    return [
        (labels[header], frozenset(labels[b] for b in body))
        for header, body in _natural_loops_idx(func)
    ]
