# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'a': 1} {'a': 1}
# {'b': 2} {'b': 2}
xs = [{'a': 1}, {'b': 2}]
for a, b in zip(xs, xs):
    print(a, b)
