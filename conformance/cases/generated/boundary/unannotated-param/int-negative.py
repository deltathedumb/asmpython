# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# -7
# True
# int
def _through(x):
    return x

def move(v):
    return _through(v)

_original = -7
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
