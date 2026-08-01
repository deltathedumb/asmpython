# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1+2j)
# True
# complex
def move(v):
    box = []
    box.append(v)
    return box[0]

_original = (1+2j)
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
