# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [[3], [1], [2]]
xs = [3, 1, 2]
print([[v] for v in xs])
