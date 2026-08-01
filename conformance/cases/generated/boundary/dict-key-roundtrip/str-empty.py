# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
#
# True
# str
def move(v):
    d = {v: 'x'}
    for k in d:
        return k

_original = ''
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
