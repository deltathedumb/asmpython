# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'k': 1}
# True
# dict
def _make(x):
    def get():
        return x
    return get

def move(v):
    return _make(v)()

_original = {'k': 1}
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
