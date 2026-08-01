# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 2
xs = [3.5, 1.5, 2.5]
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
