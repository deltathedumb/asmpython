# tier: spec
# ref: library/stdtypes.html#comparisons
# expect:
# True
# True
# [(1, 'a'), (1, 'b'), (2, 'a')]
# (2, 0)
print((1, "b") < (1, "c"))
print((1,) < (1, 0))
print(sorted([(2, "a"), (1, "b"), (1, "a")]))
print(max([(1, 9), (2, 0)]))
