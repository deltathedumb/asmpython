# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# [1, 'two', 3.5, True, None]
xs = [1, 'two', 3.5, True, None]
def give():
    return xs

print(give()[0])
print(give())
