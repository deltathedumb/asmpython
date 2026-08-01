# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# [b'cd']
xs = [b'ab', b'cd']
ys = list(xs)
ys.remove(xs[0])
print(len(ys))
print(ys)
