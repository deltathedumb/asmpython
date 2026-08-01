# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# None
# True
# NoneType
def move(v):
    return v

_original = None
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
