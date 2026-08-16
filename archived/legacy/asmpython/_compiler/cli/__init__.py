"""Command-line surface: argument handling and the subcommand groups.

These are the entry points a user reaches through `asmpython <command>`. They
sit apart from the compiler proper because they are the one part of this tree
whose shape is set by usability rather than by compilation -- and because
grouping them makes it obvious which modules are reachable from a terminal and
which are internal.
"""
