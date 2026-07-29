# expect-error: nested unpacking in a loop target is not supported natively

# NOTE: the expect block above must stay a SINGLE line -- _parse_expect joins
# every contiguous comment line after the marker into the expected text, so a
# prose paragraph attached to it becomes part of the string being searched for.
#
# Before P026 this compiled CLEAN and printed
# `1 8528656 3347130442757403493` where CPython prints `1 2 3`.
#
# `_parse_for_target` flattened `a, (b, c)` to `['a', 'b', 'c']`, producing an
# AST byte-identical to `for a, b, c in ...`. The destructuring was not merely
# unimplemented, it was INVISIBLE: the loop read a 2-tuple into 3 slots, bound
# `b` to the inner tuple's address and `c` to whatever followed it in memory.
#
# Refusing it is a CompileError, so the driver hands the program to the
# interpreter fallback, which runs it correctly -- the same path the equivalent
# assignment `a, (b, c) = (1, (2, 3))` already took. This file is checked with
# --no-pyinbin-fallback so it sees the native diagnostic itself.
for a, (b, c) in [(1, (2, 3))]:
    print(a, b, c)
