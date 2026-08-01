# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [b'ab']
# [b'cd', b'ab']
xs = [b'ab', b'cd']
print(xs[::2])
print(xs[::-1])
