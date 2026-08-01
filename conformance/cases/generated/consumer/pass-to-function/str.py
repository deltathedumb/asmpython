# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# c
xs = ['c', 'a', 'b']
def take(seq):
    return seq[0]

print(take(xs))
