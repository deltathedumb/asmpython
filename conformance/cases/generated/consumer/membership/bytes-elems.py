# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# True
xs = [b'ab', b'cd']
print(xs[0] in xs)
print(xs[-1] in xs)
