# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# False
# True
# bool
def _make(x):
    def get():
        return x
    return get

def move(v):
    return _make(v)()

_original = False
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
