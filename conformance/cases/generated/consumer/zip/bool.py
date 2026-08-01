# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True True
# False False
# True True
xs = [True, False, True]
for a, b in zip(xs, xs):
    print(a, b)
