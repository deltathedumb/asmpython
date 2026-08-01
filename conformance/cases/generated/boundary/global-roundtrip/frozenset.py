# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# frozenset({1, 2})
# True
# frozenset
_slot = None

def _put(x):
    global _slot
    _slot = x

def move(v):
    _put(v)
    return _slot

_original = frozenset([1, 2])
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
