# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 0
# True
# int
class _Box:
    def __init__(self, v):
        self.v = v

def move(v):
    return _Box(v).v

_original = 0
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
