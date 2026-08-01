# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# abc
# 0
# None
def outer(v):
    return inner(v)

def inner(v):
    return v

print(outer('abc'))
print(outer(0))
print(outer(None))
