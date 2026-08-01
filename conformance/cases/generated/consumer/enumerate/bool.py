# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 True
# 1 False
# 2 True
xs = [True, False, True]
for i, v in enumerate(xs):
    print(i, v)
