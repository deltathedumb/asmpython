# tier: spec
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# [1, 2] (1, 2) ab
# TypeError
# TypeError
# list tuple
print([1] + [2], (1,) + (2,), "a" + "b")
try:
    [1] + (2,)
except TypeError:
    print("TypeError")
try:
    "a" + 1
except TypeError:
    print("TypeError")
print(type([1] * 2).__name__, type((1,) * 2).__name__)
