# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 3.5
# 1 1.5
# 2 2.5
xs = [3.5, 1.5, 2.5]
for i, v in enumerate(xs):
    print(i, v)
