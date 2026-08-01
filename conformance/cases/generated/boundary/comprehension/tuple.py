# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'two')
# True
# tuple
def move(v):
    return [x for x in [v]][0]

_original = (1, 'two')
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
