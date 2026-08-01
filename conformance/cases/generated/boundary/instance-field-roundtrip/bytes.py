# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# True
# bytes
class _Box:
    def __init__(self, v):
        self.v = v

def move(v):
    return _Box(v).v

_original = b'ab'
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
