# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 1
xs = [{'a': 1}, {'b': 2}]
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
