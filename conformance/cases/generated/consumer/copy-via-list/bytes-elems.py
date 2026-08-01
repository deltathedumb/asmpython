# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [b'ab', b'cd']
# True
xs = [b'ab', b'cd']
ys = list(xs)
print(ys)
print(ys == xs)
