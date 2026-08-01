# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3
xs = [3, 1, 2]
def take(*a):
    return a[0]

print(take(*xs))
