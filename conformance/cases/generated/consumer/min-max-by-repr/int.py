# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# 3
xs = [3, 1, 2]
print(min(xs, key=repr))
print(max(xs, key=repr))
