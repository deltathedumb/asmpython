# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3
# [3, 1, 2]
xs = [3, 1, 2]
def give():
    return xs

print(give()[0])
print(give())
