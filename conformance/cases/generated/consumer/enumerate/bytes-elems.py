# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 b'ab'
# 1 b'cd'
xs = [b'ab', b'cd']
for i, v in enumerate(xs):
    print(i, v)
