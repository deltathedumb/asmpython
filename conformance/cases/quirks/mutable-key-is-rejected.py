# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# TypeError
# tuple-key
# TypeError
try:
    {[1]: "x"}
except TypeError:
    print("TypeError")
d = {(1, 2): "tuple-key"}
print(d[(1, 2)])
try:
    {([1],): "x"}
except TypeError:
    print("TypeError")
