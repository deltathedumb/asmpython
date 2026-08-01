# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 42
# True
# int
def move(v):
    d = {'k': v}
    return d['k']

_original = 42
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
