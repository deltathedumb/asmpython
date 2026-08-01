# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3.5
xs = [3.5, 1.5, 2.5]
def take(*a):
    return a[0]

print(take(*xs))
