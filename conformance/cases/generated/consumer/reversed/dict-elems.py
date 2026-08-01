# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [{'b': 2}, {'a': 1}]
xs = [{'a': 1}, {'b': 2}]
print(list(reversed(xs)))
