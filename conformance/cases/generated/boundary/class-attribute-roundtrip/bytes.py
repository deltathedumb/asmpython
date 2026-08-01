# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# True
# bytes
class _Holder:
    attr = None

def move(v):
    _Holder.attr = v
    return _Holder.attr

_original = b'ab'
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
