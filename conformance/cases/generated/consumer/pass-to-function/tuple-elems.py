# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'a')
xs = [(1, 'a'), (2, 'b')]
def take(seq):
    return seq[0]

print(take(xs))
