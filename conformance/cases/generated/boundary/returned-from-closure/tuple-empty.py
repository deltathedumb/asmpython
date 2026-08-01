# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# ()
# True
# tuple
def _make(x):
    def get():
        return x
    return get

def move(v):
    return _make(v)()

_original = ()
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
