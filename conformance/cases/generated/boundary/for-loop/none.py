# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# None
# True
# NoneType
def move(v):
    out = v
    for x in [v]:
        out = x
    return out

_original = None
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
