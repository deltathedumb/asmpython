# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# True
# bool
def move(v):
    d = {'k': v}
    return d['k']

_original = True
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
