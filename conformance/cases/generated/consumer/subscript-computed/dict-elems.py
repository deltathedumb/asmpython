# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'b': 2}
xs = [{'a': 1}, {'b': 2}]
i = len(xs) // 2
print(xs[i])
