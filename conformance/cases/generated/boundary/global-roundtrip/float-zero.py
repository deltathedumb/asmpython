# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0.0
# True
# float
_slot = None

def _put(x):
    global _slot
    _slot = x

def move(v):
    _put(v)
    return _slot

_original = 0.0
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
