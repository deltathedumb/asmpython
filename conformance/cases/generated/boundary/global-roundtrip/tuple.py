# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'two')
# True
# tuple
_slot = None

def _put(x):
    global _slot
    _slot = x

def move(v):
    _put(v)
    return _slot

_original = (1, 'two')
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
