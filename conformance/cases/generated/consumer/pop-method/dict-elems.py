# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'b': 2}
# 1
xs = [{'a': 1}, {'b': 2}]
ys = list(xs)
print(ys.pop())
print(len(ys))
