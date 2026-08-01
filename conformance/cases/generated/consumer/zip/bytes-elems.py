# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab' b'ab'
# b'cd' b'cd'
xs = [b'ab', b'cd']
for a, b in zip(xs, xs):
    print(a, b)
