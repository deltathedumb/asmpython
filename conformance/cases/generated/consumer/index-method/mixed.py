# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 4
xs = [1, 'two', 3.5, True, None]
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
