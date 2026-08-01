# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# frozenset({1, 2})
# True
# frozenset
def move(v):
    box = []
    box.append(v)
    return box[0]

_original = frozenset([1, 2])
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
