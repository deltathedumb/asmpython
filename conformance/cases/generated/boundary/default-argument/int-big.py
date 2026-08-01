# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 9223372036854775808
# True
# int
def move(v):
    def _inner(x=None):
        return x
    return _inner(v)

_original = 9223372036854775808
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
