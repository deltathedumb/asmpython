# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# c c
# a a
# b b
xs = ['c', 'a', 'b']
for a, b in zip(xs, xs):
    print(a, b)
