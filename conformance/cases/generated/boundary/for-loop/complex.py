# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1+2j)
# True
# complex
def move(v):
    out = v
    for x in [v]:
        out = x
    return out

_original = (1+2j)
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
