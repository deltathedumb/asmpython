# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (2, 'b')
xs = [(1, 'a'), (2, 'b')]
i = len(xs) // 2
print(xs[i])
