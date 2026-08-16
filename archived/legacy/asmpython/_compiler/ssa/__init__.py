"""The target-neutral SSA IR the legacy compiler lowers to.

The types, the lowering from post-sema AST, the text printer, the structural
verifier, the CFG analysis, and IR freezing -- one package because they are one
representation, and nothing outside needs more than a handful of entry points.

Named `ssa` rather than `ir` because `ir.py` keeps its name inside it, and
because SSA is the property that distinguishes this representation.
"""
