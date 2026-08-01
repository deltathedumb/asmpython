# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# frozenset({1, 2})
# True
# frozenset
def move(v):
    def _inner(x=None):
        return x
    return _inner(v)

_original = frozenset([1, 2])
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
