# tier: spec
# ref: library/stdtypes.html#frozenset
# expect:
# v
# frozenset
# TypeError
f = frozenset([1, 2])
d = {f: "v"}
print(d[frozenset([2, 1])])
print(type(f).__name__)
try:
    hash({1, 2})
except TypeError:
    print("TypeError")
