# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1.5
# 3.5
xs = [3.5, 1.5, 2.5]
print(min(xs, key=repr))
print(max(xs, key=repr))
