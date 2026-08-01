# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# [{'b': 2}]
xs = [{'a': 1}, {'b': 2}]
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
