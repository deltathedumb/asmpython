# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'a': 1}
# {'b': 2}
xs = [{'a': 1}, {'b': 2}]
print(xs[0])
print(xs[len(xs) - 1])
