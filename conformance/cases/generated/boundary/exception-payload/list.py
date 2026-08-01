# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 2]
# True
# list
class _E(Exception):
    pass

def move(v):
    try:
        raise _E(v)
    except _E as e:
        return e.args[0]

_original = [1, 2]
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
