# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# 1
xs = [b'ab', b'cd']
print(xs.index(xs[0]))
print(xs.index(xs[-1]))
