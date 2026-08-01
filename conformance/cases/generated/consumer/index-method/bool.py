# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 0
xs = [True, False, True]
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
