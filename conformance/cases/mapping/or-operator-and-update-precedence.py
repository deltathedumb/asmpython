# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# [('x', 1), ('y', 20), ('z', 30)]
# [('x', 1), ('y', 2), ('z', 30)]
# [('x', 1), ('y', 20), ('z', 30)]
# [('x', 1), ('y', 2)]
a = {"x": 1, "y": 2}
b = {"y": 20, "z": 30}
print(sorted((a | b).items()))
print(sorted((b | a).items()))
c = dict(a)
c |= b
print(sorted(c.items()))
print(sorted(a.items()))
