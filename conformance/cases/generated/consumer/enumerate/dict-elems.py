# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 {'a': 1}
# 1 {'b': 2}
xs = [{'a': 1}, {'b': 2}]
for i, v in enumerate(xs):
    print(i, v)
