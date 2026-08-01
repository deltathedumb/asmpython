# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'a': 1}
xs = [{'a': 1}, {'b': 2}]
def take(*a):
    return a[0]

print(take(*xs))
