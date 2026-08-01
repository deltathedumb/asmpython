# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3.5
# True
# float
def _inner(x):
    return x

def _outer(x):
    return _inner(x)

def move(v):
    return _outer(v)

_original = 3.5
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
