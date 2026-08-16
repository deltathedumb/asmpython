# expect:
# (1, 2)
# (1, 'a')
# ([1], 2)
# (1, [2])
# ([1], 'x', 2.5)
# (['a', 'b'], 1)
# ([1.5, 2.5], 1)
# ({'k': 1}, 2)
# ([1], [2])
# [[1], [2]]
# {'a': [1, 2]}
# [(1, 2), (3, 4)]

# A CONTAINER inside a TUPLE renders as its pointer, because
# _emit_tuple_repr_value asked _value_repr_kind for each slot and that answers 0
# (int) for "list"/"dict"/"tuple" -- so the slot's pointer was formatted as an
# integer: `print(([1], 2))` gave `(8550672, 2)`.
#
# Mixed SCALARS in a tuple were always fine, and the last three lines below --
# list-in-list, list-in-dict, tuple-in-list -- were fine too, because those
# paths already compose through _composite_repr_kind. Only the tuple path did
# not, so they are here to pin that the fix did not disturb them.
#
# The element kind is recovered from the slot's own EXPRESSION rather than
# assumed: defaulting the inner type to "int" would have fixed `([1], 2)` and
# silently corrupted `(['a', 'b'], 1)`, printing string pointers as integers.
# That is why the str, float and dict slots below are all covered separately.
print((1, 2))
print((1, "a"))
print(([1], 2))
print((1, [2]))
print(([1], "x", 2.5))
print((["a", "b"], 1))
print(([1.5, 2.5], 1))
print(({"k": 1}, 2))
print(([1], [2]))
print([[1], [2]])
print({"a": [1, 2]})
print([(1, 2), (3, 4)])
