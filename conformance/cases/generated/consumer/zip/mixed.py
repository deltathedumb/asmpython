# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1 1
# two two
# 3.5 3.5
# True True
# None None
xs = [1, 'two', 3.5, True, None]
for a, b in zip(xs, xs):
    print(a, b)
