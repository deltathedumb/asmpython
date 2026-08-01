# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
xs = [True, False, True]
def take(seq):
    return seq[0]

print(take(xs))
