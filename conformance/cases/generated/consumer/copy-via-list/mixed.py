# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 'two', 3.5, True, None]
# True
xs = [1, 'two', 3.5, True, None]
ys = list(xs)
print(ys)
print(ys == xs)
