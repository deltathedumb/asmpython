# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3.5
# True
# float
def move(v):
    d = {v: 'x'}
    for k in d:
        return k

_original = 3.5
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
