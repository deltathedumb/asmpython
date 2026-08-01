# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 [1, 2]
# 1 [3]
# 2 [4, 5, 6]
xs = [[1, 2], [3], [4, 5, 6]]
for i, v in enumerate(xs):
    print(i, v)
