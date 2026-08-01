# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# True
# bool
def _through(*a):
    return a[0]

def move(v):
    return _through(v)

_original = True
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
