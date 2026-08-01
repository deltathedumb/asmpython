# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# True
# bytes
def move(v):
    return [x for x in [v]][0]

_original = b'ab'
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
