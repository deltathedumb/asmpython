# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 c
# 1 a
# 2 b
xs = ['c', 'a', 'b']
for i, v in enumerate(xs):
    print(i, v)
