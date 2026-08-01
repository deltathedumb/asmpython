# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# True
# int
def _through(x: object):
    return x

def move(v):
    return _through(v)

_original = 0
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
