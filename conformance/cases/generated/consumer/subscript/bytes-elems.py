# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# b'cd'
xs = [b'ab', b'cd']
print(xs[0])
print(xs[len(xs) - 1])
