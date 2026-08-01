# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# [True, False, True]
xs = [True, False, True]
def give():
    return xs

print(give()[0])
print(give())
