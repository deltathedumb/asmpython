# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# c
xs = ['c', 'a', 'b']
def take(*a):
    return a[0]

print(take(*xs))
