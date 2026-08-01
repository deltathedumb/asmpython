# tier: spec
# ref: library/stdtypes.html#dict.fromkeys
# expect:
# [('a', 0), ('b', 0)]
# [1]
# 0 9
d = dict.fromkeys(["a", "b"], 0)
print(sorted(d.items()))
shared = dict.fromkeys(["a", "b"], [])
shared["a"].append(1)
print(shared["b"])
c = d.copy()
c["a"] = 9
print(d["a"], c["a"])
