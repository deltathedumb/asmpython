# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 2]
# [[1, 2], [3], [4, 5, 6]]
xs = [[1, 2], [3], [4, 5, 6]]
def give():
    return xs

print(give()[0])
print(give())
