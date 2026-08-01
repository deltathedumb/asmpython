# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 9223372036854775808
# True
# int
def _through(*a):
    return a[0]

def move(v):
    return _through(v)

_original = 9223372036854775808
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
