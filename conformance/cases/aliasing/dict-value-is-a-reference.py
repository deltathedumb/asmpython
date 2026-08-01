# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# [('n', 2)]
# True
shared = {"n": 1}
d = {"a": shared, "b": shared}
d["a"]["n"] = 2
print(sorted(d["b"].items()))
print(d["a"] is d["b"])
