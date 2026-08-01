# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
xs = [b'ab', b'cd']
def take(seq):
    return seq[0]

print(take(xs))
