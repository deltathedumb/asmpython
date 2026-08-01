# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
xs = [True, False, True]
def take(*a):
    return a[0]

print(take(*xs))
