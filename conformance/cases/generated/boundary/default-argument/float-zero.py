# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0.0
# True
# float
def move(v):
    def _inner(x=None):
        return x
    return _inner(v)

_original = 0.0
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
