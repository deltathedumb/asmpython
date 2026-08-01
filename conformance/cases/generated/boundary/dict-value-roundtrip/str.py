# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# abc
# True
# str
def move(v):
    d = {'k': v}
    return d['k']

_original = 'abc'
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
