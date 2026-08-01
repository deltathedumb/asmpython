# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3.5
# [3.5, 1.5, 2.5]
xs = [3.5, 1.5, 2.5]
def give():
    return xs

print(give()[0])
print(give())
