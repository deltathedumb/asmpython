# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 3
# 1 1
# 2 2
xs = [3, 1, 2]
for i, v in enumerate(xs):
    print(i, v)
