# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# frozenset({1, 2})
# True
# frozenset
def _through(*a):
    return a[0]

def move(v):
    return _through(v)

_original = frozenset([1, 2])
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
