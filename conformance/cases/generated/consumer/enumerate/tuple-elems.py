# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0 (1, 'a')
# 1 (2, 'b')
xs = [(1, 'a'), (2, 'b')]
for i, v in enumerate(xs):
    print(i, v)
