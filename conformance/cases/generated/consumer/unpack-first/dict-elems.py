# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'a': 1}
# [{'b': 2}]
xs = [{'a': 1}, {'b': 2}]
head = xs[0]
rest = xs[1:]
print(head)
print(rest)
