# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3 3
# 1 1
# 2 2
xs = [3, 1, 2]
for a, b in zip(xs, xs):
    print(a, b)
