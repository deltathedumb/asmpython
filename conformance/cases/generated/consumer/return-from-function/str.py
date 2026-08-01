# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# c
# ['c', 'a', 'b']
xs = ['c', 'a', 'b']
def give():
    return xs

print(give()[0])
print(give())
