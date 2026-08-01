# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# [b'ab', b'cd']
xs = [b'ab', b'cd']
def give():
    return xs

print(give()[0])
print(give())
