# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# True
xs = [{'a': 1}, {'b': 2}]
print(xs[0] in xs)
print(xs[-1] in xs)
