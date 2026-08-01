# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 1
# 1 two
# 2 3.5
# 3 True
# 4 None
xs = [1, 'two', 3.5, True, None]
for i, v in enumerate(xs):
    print(i, v)
