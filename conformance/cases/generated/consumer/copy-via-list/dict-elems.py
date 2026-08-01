# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [{'a': 1}, {'b': 2}]
# True
xs = [{'a': 1}, {'b': 2}]
ys = list(xs)
print(ys)
print(ys == xs)
