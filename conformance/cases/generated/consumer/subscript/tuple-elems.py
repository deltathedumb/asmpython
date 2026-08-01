# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'a')
# (2, 'b')
xs = [(1, 'a'), (2, 'b')]
print(xs[0])
print(xs[len(xs) - 1])
