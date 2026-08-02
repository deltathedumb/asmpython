"""Incremental compilation: the fragment cache and the state behind it.

Records per-function assembly ranges, assembles each into its own object, and
reuses the object whenever the fragment's digest is unchanged. Separate from
`build/` because that package decides what a build does, while this one is an
optimisation that must be invisible -- an incremental build and a clean build
have to produce the same program, and keeping the machinery in one place is
what makes that checkable.
"""
