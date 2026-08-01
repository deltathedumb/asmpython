# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ()
# True
# tuple
class _Holder:
    attr = None

def move(v):
    _Holder.attr = v
    return _Holder.attr

_original = ()
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
