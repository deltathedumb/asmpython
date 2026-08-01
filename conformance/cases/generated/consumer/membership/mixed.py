# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# True
xs = [1, 'two', 3.5, True, None]
print(xs[0] in xs)
print(xs[-1] in xs)
