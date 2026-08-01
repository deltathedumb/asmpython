# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
xs = [1, 'two', 3.5, True, None]
def take(seq):
    return seq[0]

print(take(xs))
