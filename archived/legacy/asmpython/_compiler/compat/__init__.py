"""Behaviour-compatibility passes, applied after sema and during lowering.

Twenty-six modules that each repair one class of divergence between what
the analyser inferred and what the language actually requires. They were
flat in `_compiler/` and made up a quarter of its files while forming one
obvious family; grouping them is a move, not a rewrite -- no logic here has
changed.

Each module keeps its own name so the history and every code comment that
cites one still resolve.
"""
